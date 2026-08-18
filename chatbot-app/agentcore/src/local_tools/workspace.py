"""Shared workspace tools — read/write files across all skill namespaces.

Path conventions (userId/sessionId are injected automatically from context):
  uploads/<file>              →  code-interpreter-workspace/{workspaceId}/inputs/<file>
  code-agent/<file>           →  code-agent-workspace/{userId}/{sessionId}/<file>
  documents/<type>/<file>     →  documents/{userId}/{sessionId}/<type>/<file>

Both namespaces share the same ARTIFACT_BUCKET.
"""

import base64
import json
import logging
import os

import boto3
import botocore.exceptions
from strands import tool, ToolContext
from skill import register_skill
from workspace.config import get_workspace_bucket
from workspace.paths import code_interpreter_prefix

logger = logging.getLogger(__name__)

# Extensions treated as binary. Binary payloads must not be returned as text:
# a single base64-encoded Office file can exceed the model context window.
_BINARY_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.svg',
    '.pdf', '.pptx', '.ppt', '.docx', '.doc', '.xlsx', '.xls',
    '.zip', '.tar', '.gz', '.mp4', '.mp3', '.wav',
}
_TEXT_READ_MAX_BYTES = 100_000


def _get_ids(context: ToolContext):
    state = context.invocation_state
    return state.get('user_id', 'default_user'), state.get('session_id', 'default_session')


def _namespace_map(user_id: str, session_id: str):
    ci_prefix = code_interpreter_prefix(user_id, session_id)
    return [
        ('uploads',          f'{ci_prefix}inputs/'),
        ('code-agent',       f'code-agent-workspace/{user_id}/{session_id}/'),
        ('code-interpreter', ci_prefix),
        ('documents',        f'documents/{user_id}/{session_id}/'),
    ]


def _to_s3_key(user_id: str, session_id: str, path: str) -> str:
    """Map logical path to S3 key."""
    path = path.lstrip('/')
    for prefix, base in _namespace_map(user_id, session_id):
        if path == prefix or path.startswith(prefix + '/'):
            suffix = path[len(prefix):].lstrip('/')
            return base + suffix
    # default: documents namespace
    return f"documents/{user_id}/{session_id}/{path}"


def _to_logical_path(user_id: str, session_id: str, s3_key: str) -> str:
    """Convert S3 key back to logical path."""
    for prefix, s3_base in _namespace_map(user_id, session_id):
        if s3_key.startswith(s3_base):
            return prefix + '/' + s3_key[len(s3_base):]
    return s3_key


def _is_binary(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in _BINARY_EXTENSIONS


def _s3_client():
    return boto3.client('s3', region_name=os.getenv('AWS_REGION', 'us-west-2'))


@tool(context=True)
def workspace_list(path: str = '', tool_context: ToolContext = None) -> str:
    """List files in the shared session workspace.

    Args:
        path: Optional prefix to filter results.
              ''                      — list all namespaces
              'uploads/'              — files uploaded by the user
              'code-agent/'           — files created by the code agent
              'documents/'            — office/document files
              'documents/powerpoint/' — narrow to a specific type

    Returns:
        JSON with a list of files, each with path, size, and last_modified.
    """
    try:
        user_id, session_id = _get_ids(tool_context)
        bucket = get_workspace_bucket()
        s3 = _s3_client()

        if not path or path.strip('/') == '':
            ci_prefix = code_interpreter_prefix(user_id, session_id)
            s3_prefixes = [
                f"{ci_prefix}inputs/",
                f"code-agent-workspace/{user_id}/{session_id}/",
                ci_prefix,
                f"documents/{user_id}/{session_id}/",
            ]
        else:
            s3_prefixes = [_to_s3_key(user_id, session_id, path.rstrip('/') + '/')]

        files = []
        seen_paths = set()
        for prefix in s3_prefixes:
            paginator = s3.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    if not key.endswith('/'):
                        logical_path = _to_logical_path(user_id, session_id, key)
                        if logical_path in seen_paths:
                            continue
                        seen_paths.add(logical_path)
                        files.append({
                            'path': logical_path,
                            'size': obj['Size'],
                            'last_modified': obj['LastModified'].isoformat(),
                        })

        return json.dumps({'files': files, 'count': len(files)}, default=str)

    except Exception as e:
        logger.error(f"[workspace_list] {e}")
        return json.dumps({'error': str(e), 'status': 'error'})


@tool(context=True)
def workspace_read(path: str, tool_context: ToolContext = None) -> str:
    """Read a file from the shared session workspace.

    Text files are returned as plain strings.
    Binary files return metadata only; use the relevant document tool or Code
    Interpreter to inspect their contents.

    Args:
        path: Logical path, e.g.:
              'uploads/data.jsonl'
              'code-agent/calculator.png'
              'documents/powerpoint/report.pptx'
              'documents/image/chart.png'

    Returns:
        JSON with text content, or metadata for binary files.
    """
    try:
        user_id, session_id = _get_ids(tool_context)
        bucket = get_workspace_bucket()
        s3 = _s3_client()

        s3_key = _to_s3_key(user_id, session_id, path)
        response = s3.get_object(Bucket=bucket, Key=s3_key)
        if _is_binary(path):
            size = int(response.get('ContentLength', 0))
            return json.dumps({
                'path': path,
                'encoding': 'binary',
                'size': size,
                'content_omitted': True,
                'message': (
                    'Binary content is not returned inline. Use the appropriate '
                    'document/presentation tool or Code Interpreter to inspect this file.'
                ),
                'status': 'ok',
            })
        else:
            data = response['Body'].read(_TEXT_READ_MAX_BYTES + 1)
            truncated = (
                len(data) > _TEXT_READ_MAX_BYTES
                or response.get('ContentLength', 0) > _TEXT_READ_MAX_BYTES
            )
            if len(data) > _TEXT_READ_MAX_BYTES:
                data = data[:_TEXT_READ_MAX_BYTES]
            return json.dumps({
                'path': path,
                'encoding': 'text',
                'content': data.decode('utf-8', errors='replace'),
                'size': len(data),
                'truncated': truncated,
                **({
                    'full_file': (
                        'Use Code Interpreter to read the complete file. '
                        f'With the workspace mount use '
                        f'/mnt/workspace/inputs/{os.path.basename(path)}; '
                        f'legacy sessions preload it as '
                        f'{os.path.basename(path)} in the working directory.'
                    ),
                } if truncated and path.startswith('uploads/') else {}),
                'status': 'ok',
            })

    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            return json.dumps({'error': f"File not found: {path}", 'status': 'error'})
        logger.error(f"[workspace_read] S3 error: {e}")
        return json.dumps({'error': str(e), 'status': 'error'})
    except Exception as e:
        logger.error(f"[workspace_read] {e}")
        return json.dumps({'error': str(e), 'status': 'error'})


@tool(context=True)
def workspace_write(
    path: str,
    content: str,
    encoding: str = 'text',
    tool_context: ToolContext = None,
) -> str:
    """Write a file to the shared session workspace.

    Args:
        path: Logical path (e.g. 'documents/image/chart.png').
        content: File content — plain text if encoding='text',
                 base64-encoded string if encoding='base64'.
        encoding: 'text' (default) or 'base64' for binary files.

    Returns:
        JSON with confirmation and resolved path.
    """
    try:
        user_id, session_id = _get_ids(tool_context)
        bucket = get_workspace_bucket()
        s3 = _s3_client()

        s3_key = _to_s3_key(user_id, session_id, path)
        data = base64.b64decode(content) if encoding == 'base64' else content.encode('utf-8')

        s3.put_object(Bucket=bucket, Key=s3_key, Body=data)
        logger.info(f"[workspace_write] {len(data)} bytes → s3://{bucket}/{s3_key}")

        return json.dumps({
            'path': path,
            'size': len(data),
            'status': 'ok',
        })

    except Exception as e:
        logger.error(f"[workspace_write] {e}")
        return json.dumps({'error': str(e), 'status': 'error'})


register_skill('workspace', tools=[workspace_list, workspace_read, workspace_write])
