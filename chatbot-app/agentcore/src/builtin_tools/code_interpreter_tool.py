"""General-purpose Code Interpreter tools using AWS Bedrock AgentCore Code Interpreter.

Provides 4 tools wrapping CodeInterpreter (bedrock_agentcore):
  - execute_code:    Run Python/JS/TS code
  - execute_command: Run shell commands
  - file_operations: Read/write/list/remove files in the sandbox
  - ci_push_to_workspace: Save sandbox files to S3

All tools share a single persistent CI session per user/session — files and
variables persist across tool calls. Word, Excel, and other document tools
use the same session via get_ci_session().

Session ID is cached in agent.state so it survives across turns without
creating new sessions. If a session times out (15 min idle), a new one is
created automatically and workspace files are re-synced.
"""

from strands import tool, ToolContext
from skill import register_skill
from typing import Any, Dict, List, Optional
import json
import logging
import os

logger = logging.getLogger(__name__)

# In-process CI client cache: session_key → CodeInterpreter
# This avoids re-creating CodeInterpreter objects within the same process.
# The actual session_id is stored in agent.state for cross-turn persistence.
_ci_clients: Dict[str, Any] = {}

# Tracks which S3 keys have been synced to legacy CI sessions to avoid re-downloading.
# Key: session_key (user_id-session_id), Value: set of S3 object keys already loaded.
_synced_s3_keys: Dict[str, set] = {}

# agent.state keys for CI session persistence
_STATE_CI_SESSION_ID = "ci_session_id"
_STATE_CI_IDENTIFIER = "ci_identifier"
_STATE_CI_MOUNTED_WORKSPACE = "ci_mounted_workspace"

# Session timeout in seconds (1 hour; max is 28800 = 8 hours)
_SESSION_TIMEOUT_SECONDS = 3600


def invalidate_session(user_id: str, session_id: str) -> None:
    """Remove cached CI client so next tool call creates a fresh session.

    Called by file_processor.auto_store_files() after uploading new files,
    so the next tool call will re-create the session and preload workspace.
    """
    session_key = f"{user_id}-{session_id}"
    if session_key in _ci_clients:
        try:
            _ci_clients[session_key].stop()
        except Exception:
            pass
        del _ci_clients[session_key]
        logger.info(f"Invalidated CI client cache: {session_key}")
    _synced_s3_keys.pop(session_key, None)



def _get_code_interpreter_id() -> Optional[str]:
    """Get Code Interpreter ID from environment or Parameter Store."""
    ci_id = os.getenv('CODE_INTERPRETER_ID')
    if ci_id:
        return ci_id
    try:
        import boto3
        project_name = os.getenv('PROJECT_NAME', 'strands-agent-chatbot')
        environment = os.getenv('ENVIRONMENT', 'dev')
        region = os.getenv('AWS_REGION', 'us-west-2')
        param_name = f"/{project_name}/{environment}/agentcore/code-interpreter-id"
        ssm = boto3.client('ssm', region_name=region)
        response = ssm.get_parameter(Name=param_name)
        return response['Parameter']['Value']
    except Exception as e:
        logger.warning(f"Code Interpreter ID not found: {e}")
        return None


def _is_session_alive(ci: Any, identifier: str, ci_session_id: str) -> bool:
    """Check if a CI session is still READY (not timed out)."""
    try:
        response = ci.get_session(interpreter_id=identifier, session_id=ci_session_id)
        status = response.get("status", "UNKNOWN")
        logger.debug(f"CI session {ci_session_id} status: {status}")
        return status == "READY"
    except Exception as e:
        logger.warning(f"CI session health check failed: {e}")
        return False


def get_ci_session(tool_context: ToolContext) -> Optional[Any]:
    """Get or create a shared CodeInterpreter session for all tools.

    Session lifecycle:
    1. Check in-process cache (_ci_clients) for existing client
    2. Check agent.state for stored session_id (cross-turn persistence)
    3. Verify session is still READY via get_session() API
    4. If expired/missing, create new session and preload workspace files
    5. Store new session_id in agent.state

    All tools (execute_code, word, excel, powerpoint, etc.) call this to get
    the same persistent sandbox. Never call .start() or .stop() on the result.

    Args:
        tool_context: Strands ToolContext (provides agent.state + invocation_state)

    Returns:
        CodeInterpreter instance, or None if CODE_INTERPRETER_ID not configured.
    """
    from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter

    invocation_state = tool_context.invocation_state
    user_id = invocation_state.get('user_id', 'default_user')
    session_id = invocation_state.get('session_id', 'default_session')
    session_key = f"{user_id}-{session_id}"
    region = os.getenv('AWS_REGION', 'us-west-2')

    # Step 1: Try in-process cache (fast path — same process, same turn or consecutive turns)
    ci = _ci_clients.get(session_key)
    if ci is not None:
        # Verify the cached client's session is still alive
        stored_id = ci.session_id
        stored_identifier = ci.identifier
        if stored_id and stored_identifier and _is_session_alive(ci, stored_identifier, stored_id):
            if tool_context.agent.state.get(_STATE_CI_MOUNTED_WORKSPACE) is not True:
                _sync_new_workspace_files(ci, user_id, session_id)
            return ci
        # Session expired — remove stale cache
        logger.info(f"Cached CI session expired: {session_key}")
        del _ci_clients[session_key]

    # Step 2: Try reattaching from agent.state (cross-turn persistence)
    agent_state = tool_context.agent.state
    stored_session_id = agent_state.get(_STATE_CI_SESSION_ID)
    stored_identifier = agent_state.get(_STATE_CI_IDENTIFIER)

    if stored_session_id and stored_identifier:
        ci = CodeInterpreter(region)
        ci.identifier = stored_identifier
        ci.session_id = stored_session_id
        if _is_session_alive(ci, stored_identifier, stored_session_id):
            logger.info(f"Reattached to existing CI session: {stored_session_id}")
            _ci_clients[session_key] = ci
            if agent_state.get(_STATE_CI_MOUNTED_WORKSPACE) is not True:
                _sync_new_workspace_files(ci, user_id, session_id)
            return ci
        logger.info(f"Stored CI session expired ({stored_session_id}), creating new one")

    # Step 3: Create a new session
    identifier = stored_identifier or _get_code_interpreter_id()
    if not identifier:
        return None

    ci = CodeInterpreter(region)
    mounted_workspace = False
    try:
        from workspace.s3_files import get_or_create_session_access_point

        workspace = get_or_create_session_access_point(
            agent_state,
            user_id,
            session_id,
        )
        if workspace:
            response = ci.data_plane_client.start_code_interpreter_session(
                codeInterpreterIdentifier=identifier,
                name=f"workspace-{session_id[:32]}",
                sessionTimeoutSeconds=_SESSION_TIMEOUT_SECONDS,
                filesystemConfigurations=[{
                    "s3FilesConfiguration": {
                        "fileSystemArn": workspace["file_system_arn"],
                        "accessPointArn": workspace["access_point_arn"],
                        "mountPath": workspace["mount_path"],
                    },
                }],
            )
            ci.identifier = response["codeInterpreterIdentifier"]
            ci.session_id = response["sessionId"]
            mounted_workspace = True
    except Exception as error:
        logger.exception(
            "Could not start Code Interpreter with S3 Files; using legacy sync: %s",
            error,
        )

    if not mounted_workspace:
        ci.start(
            identifier=identifier,
            session_timeout_seconds=_SESSION_TIMEOUT_SECONDS,
        )
    logger.info(f"Created new CI session: {ci.session_id} (identifier: {identifier}, timeout: {_SESSION_TIMEOUT_SECONDS}s)")

    # Store in agent.state for cross-turn persistence
    agent_state.set(_STATE_CI_SESSION_ID, ci.session_id)
    agent_state.set(_STATE_CI_IDENTIFIER, identifier)
    agent_state.set(_STATE_CI_MOUNTED_WORKSPACE, mounted_workspace)

    # Cache in-process
    _ci_clients[session_key] = ci

    if mounted_workspace:
        _prepare_mounted_workspace(ci)
    else:
        _preload_workspace_files(ci, user_id, session_id)

    return ci


def _prepare_mounted_workspace(ci: Any) -> None:
    """Set the Code Interpreter working directory and trigger input import."""
    mount_path = os.getenv("S3_FILES_MOUNT_PATH", "/mnt/workspace")
    code = (
        "import os\n"
        f"os.makedirs({mount_path!r}, exist_ok=True)\n"
        f"os.chdir({mount_path!r})\n"
        "_inputs = os.path.join(os.getcwd(), 'inputs')\n"
        "if os.path.isdir(_inputs):\n"
        "    list(os.scandir(_inputs))\n"
    )
    response = ci.invoke("executeCode", {
        "code": code,
        "language": "python",
        "clearContext": False,
    })
    _, stderr, has_error = _parse_stream(response)
    if has_error:
        raise RuntimeError(f"Could not initialize mounted workspace: {stderr}")


def _uses_mounted_workspace(tool_context: ToolContext) -> bool:
    return tool_context.agent.state.get(_STATE_CI_MOUNTED_WORKSPACE) is True


def _mounted_path(path: str) -> str:
    mount_path = os.getenv("S3_FILES_MOUNT_PATH", "/mnt/workspace")
    normalized = os.path.normpath(path)
    if os.path.isabs(normalized):
        if normalized != mount_path and not normalized.startswith(f"{mount_path}/"):
            raise ValueError("Path must stay inside the session workspace")
        return normalized
    if normalized == ".." or normalized.startswith("../"):
        raise ValueError("Path must stay inside the session workspace")
    return os.path.join(mount_path, normalized)


def _prepare_code_for_mounted_workspace(code: str, language: str) -> str:
    """Run JavaScript and TypeScript from the mounted workspace."""
    if language.lower() not in {"javascript", "typescript"}:
        return code
    mount_path = os.getenv("S3_FILES_MOUNT_PATH", "/mnt/workspace")
    return f"Deno.chdir({json.dumps(mount_path)});\n{code}"


def _get_ci_from_context(tool_context: ToolContext) -> Optional[Any]:
    """Get CI session using ToolContext (convenience wrapper)."""
    return get_ci_session(tool_context)


def _parse_stream(response: dict) -> tuple:
    """Parse invoke() streaming response.

    Returns:
        (stdout: str, stderr: str, has_error: bool)
    """
    stdout_parts = []
    stderr = ""
    has_error = False
    for event in response.get("stream", []):
        result = event.get("result", {})
        if result.get("isError", False):
            has_error = True
            stderr = result.get("structuredContent", {}).get("stderr", "Unknown error")
        stdout = result.get("structuredContent", {}).get("stdout", "")
        if stdout:
            stdout_parts.append(stdout)
    return "".join(stdout_parts), stderr, has_error


def _get_workspace_s3_prefixes(user_id: str, session_id: str) -> List[str]:
    """Return S3 prefixes for all workspace document types."""
    return [
        f"code-interpreter-workspace/{user_id}/{session_id}/inputs/",
        f"documents/{user_id}/{session_id}/zip/",
        f"documents/{user_id}/{session_id}/word/",
        f"documents/{user_id}/{session_id}/excel/",
        f"documents/{user_id}/{session_id}/powerpoint/",
        f"documents/{user_id}/{session_id}/image/",
        f"documents/{user_id}/{session_id}/code-output/",
        f"documents/{user_id}/{session_id}/raw/",
    ]


def _upload_s3_file_to_ci(ci: Any, s3_client: Any, bucket: str, s3_key: str) -> None:
    """Download a single file from S3 and write it into the CI sandbox."""
    import base64

    filename = s3_key.split('/')[-1]
    if not filename:
        return
    data = s3_client.get_object(Bucket=bucket, Key=s3_key)['Body'].read()
    b64 = base64.b64encode(data).decode('utf-8')
    code = (
        f"import base64\n"
        f"with open('{filename}', 'wb') as _f:\n"
        f"    _f.write(base64.b64decode('{b64}'))\n"
        f"print('Loaded: {filename}')\n"
    )
    ci.invoke("executeCode", {
        "code": code,
        "language": "python",
        "clearContext": False,
    })


def _preload_workspace_files(ci: Any, user_id: str, session_id: str) -> None:
    """Load all workspace files from S3 into a newly created CI sandbox."""
    import boto3

    session_key = f"{user_id}-{session_id}"
    _synced_s3_keys[session_key] = set()

    try:
        from workspace.config import get_workspace_bucket
        bucket = get_workspace_bucket()
        s3 = boto3.client('s3', region_name=os.getenv('AWS_REGION', 'us-west-2'))

        for prefix in _get_workspace_s3_prefixes(user_id, session_id):
            response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            for obj in response.get('Contents', []):
                s3_key = obj['Key']
                try:
                    _upload_s3_file_to_ci(ci, s3, bucket, s3_key)
                    _synced_s3_keys[session_key].add(s3_key)
                    logger.info(f"Preloaded into CI sandbox: {s3_key.split('/')[-1]}")
                except Exception as e:
                    logger.warning(f"Failed to preload {s3_key}: {e}")

    except Exception as e:
        logger.warning(f"_preload_workspace_files failed: {e}")


def _sync_new_workspace_files(ci: Any, user_id: str, session_id: str) -> None:
    """Sync only new S3 files into an existing CI session (delta sync)."""
    import boto3

    session_key = f"{user_id}-{session_id}"
    synced = _synced_s3_keys.get(session_key)
    if synced is None:
        # No tracking info — skip rather than re-downloading everything
        _synced_s3_keys[session_key] = set()
        synced = _synced_s3_keys[session_key]

    try:
        from workspace.config import get_workspace_bucket
        bucket = get_workspace_bucket()
        s3 = boto3.client('s3', region_name=os.getenv('AWS_REGION', 'us-west-2'))

        new_count = 0
        for prefix in _get_workspace_s3_prefixes(user_id, session_id):
            response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            for obj in response.get('Contents', []):
                s3_key = obj['Key']
                if s3_key in synced:
                    continue
                try:
                    _upload_s3_file_to_ci(ci, s3, bucket, s3_key)
                    synced.add(s3_key)
                    new_count += 1
                    logger.info(f"Delta-synced to CI sandbox: {s3_key.split('/')[-1]}")
                except Exception as e:
                    logger.warning(f"Failed to delta-sync {s3_key}: {e}")

        if new_count:
            logger.info(f"Delta sync complete: {new_count} new file(s) synced to CI")

    except Exception as e:
        logger.warning(f"_sync_new_workspace_files failed: {e}")


def _resolve_doc_type(filename: str) -> str:
    """Determine workspace document type from file extension."""
    ext = filename.lower().rsplit('.', 1)[-1] if '.' in filename else ''
    if ext == 'docx':
        return 'word'
    if ext == 'xlsx':
        return 'excel'
    if ext == 'pptx':
        return 'powerpoint'
    if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'pdf'):
        return 'image'
    return 'code-output'


def _save_to_workspace(tool_context: ToolContext, filename: str, file_bytes: bytes) -> Optional[dict]:
    """Save a generated file to the workspace (S3) under the correct documents/ prefix.

    Routes files based on extension:
      .docx → word/  |  .xlsx → excel/  |  .pptx → powerpoint/
      images/pdf → image/  |  everything else → code-output/
    """
    try:
        from workspace import WordManager, ExcelManager, PowerPointManager, ImageManager
        from workspace.base_manager import BaseDocumentManager

        invocation_state = tool_context.invocation_state
        user_id = invocation_state.get('user_id', 'default_user')
        session_id = invocation_state.get('session_id', 'default_session')

        doc_type = _resolve_doc_type(filename)
        manager_map = {
            'word': lambda: WordManager(user_id, session_id),
            'excel': lambda: ExcelManager(user_id, session_id),
            'powerpoint': lambda: PowerPointManager(user_id, session_id),
            'image': lambda: ImageManager(user_id, session_id),
        }
        if doc_type in manager_map:
            manager = manager_map[doc_type]()
        else:
            manager = BaseDocumentManager(user_id, session_id, document_type='code-output')

        s3_info = manager.save_to_s3(
            filename,
            file_bytes,
            metadata={'source': 'code_interpreter_tool'},
        )
        logger.info(f"Saved to workspace: {s3_info['s3_key']} (type={doc_type})")

        # Save artifact to agent.state for Canvas display
        try:
            from datetime import datetime, timezone
            artifact_id = f"ci-{filename.rsplit('.', 1)[0]}"
            artifacts = tool_context.agent.state.get("artifacts") or {}
            artifacts[artifact_id] = {
                "id": artifact_id,
                "type": doc_type,
                "title": filename,
                "content": s3_info.get('s3_url', s3_info['s3_key']),
                "tool_name": "execute_code",
                "metadata": {
                    "filename": filename,
                    "s3_key": s3_info['s3_key'],
                    "size_kb": f"{len(file_bytes) / 1024:.1f}",
                    "doc_type": doc_type,
                },
                "created_at": artifacts.get(artifact_id, {}).get(
                    "created_at", datetime.now(timezone.utc).isoformat()
                ),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            tool_context.agent.state.set("artifacts", artifacts)

            session_manager = tool_context.invocation_state.get("session_manager")
            if not session_manager and hasattr(tool_context.agent, "session_manager"):
                session_manager = tool_context.agent.session_manager
            if session_manager:
                session_manager.sync_agent(tool_context.agent)
                logger.info(f"Saved CI artifact: {artifact_id}")
        except Exception as e:
            logger.warning(f"Failed to save CI artifact to agent.state: {e}")

        return {'s3_key': s3_info['s3_key'], 'doc_type': doc_type, 'bucket': manager.bucket}
    except Exception as e:
        logger.warning(f"Could not save to workspace: {e}")
        return None


# -----------------------------------------------------------------------
# Tool 1: execute_code
# -----------------------------------------------------------------------

@tool(context=True)
def execute_code(
    code: str,
    language: str = "python",
    output_filename: str = "",
    tool_context: ToolContext = None,
) -> str:
    """Execute code in a sandboxed Code Interpreter environment.

    Supports Python (recommended, 200+ libraries), JavaScript, and TypeScript.
    Use print() to return text results. Variables persist across calls.

    Args:
        code: Code to execute.
        language: "python" (default), "javascript", or "typescript".
        output_filename: Optional. If provided, downloads this file after execution
                        and saves it to workspace. Code must save a file with this exact name.

    Returns:
        Execution stdout, or file confirmation if output_filename is set.
    """
    ci = _get_ci_from_context(tool_context)
    if ci is None:
        return json.dumps({
            "error": "Code Interpreter not available. Deploy AgentCore Runtime Stack.",
            "status": "error",
        })

    try:
        mounted = _uses_mounted_workspace(tool_context)
        if mounted:
            _prepare_mounted_workspace(ci)
            code = _prepare_code_for_mounted_workspace(code, language)
        response = ci.invoke("executeCode", {
            "code": code,
            "language": language,
            "clearContext": False
        })
        stdout, stderr, has_error = _parse_stream(response)

        if has_error:
            return json.dumps({
                "error": stderr,
                "code_snippet": code[:300],
                "status": "error",
            })

        if not output_filename:
            return stdout or "(no output)"

        # Download output file
        output_path = _mounted_path(output_filename) if mounted else output_filename
        download_response = ci.invoke("readFiles", {"paths": [output_path]})
        for event in download_response.get("stream", []):
            result = event.get("result", {})
            for item in result.get("content", []):
                if not isinstance(item, dict):
                    continue
                blob = item.get("data") or item.get("resource", {}).get("blob")
                if blob:
                    if not mounted:
                        _save_to_workspace(tool_context, output_filename, blob)
                    size_kb = len(blob) / 1024
                    workspace_path = (
                        f"code-interpreter/{output_filename}"
                        if mounted
                        else output_filename
                    )
                    summary = f"Code executed. File saved: {workspace_path} ({size_kb:.1f} KB)"
                    if stdout:
                        summary += f"\n\nstdout:\n{stdout[:500]}"

                    lower_name = output_filename.lower()
                    if lower_name.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                        return json.dumps({
                            "content": [
                                {"text": summary},
                                {"image": {
                                    "format": "png" if lower_name.endswith(".png") else "jpeg",
                                    "source": {"bytes": "__IMAGE_BYTES__"},
                                }},
                            ],
                            "status": "success",
                        })
                    return summary

        return json.dumps({
            "warning": f"Code executed but could not download '{output_filename}'.",
            "stdout": stdout[:500] if stdout else "(none)",
            "status": "partial",
        })

    except Exception as e:
        logger.error(f"execute_code error: {e}")
        return json.dumps({"error": str(e), "status": "error"})


# -----------------------------------------------------------------------
# Tool 2: execute_command
# -----------------------------------------------------------------------

@tool(context=True)
def execute_command(
    command: str,
    tool_context: ToolContext = None,
) -> str:
    """Execute a shell command in the Code Interpreter sandbox.

    Useful for: installing packages (pip install), listing files (ls),
    checking environment (python --version), running scripts, etc.

    Args:
        command: Shell command to execute (e.g. "ls -la", "pip install requests").

    Returns:
        Command stdout/stderr output.
    """
    ci = _get_ci_from_context(tool_context)
    if ci is None:
        return json.dumps({
            "error": "Code Interpreter not available. Deploy AgentCore Runtime Stack.",
            "status": "error",
        })

    try:
        if _uses_mounted_workspace(tool_context):
            mount_path = os.getenv("S3_FILES_MOUNT_PATH", "/mnt/workspace")
            command = f"cd {json.dumps(mount_path)} && {command}"
        response = ci.invoke("executeCommand", {"command": command})
        stdout, stderr, has_error = _parse_stream(response)
        if has_error:
            return json.dumps({"error": stderr, "status": "error"})
        return stdout or "(no output)"

    except Exception as e:
        logger.error(f"execute_command error: {e}")
        return json.dumps({"error": str(e), "status": "error"})


# -----------------------------------------------------------------------
# Tool 3: file_operations
# -----------------------------------------------------------------------

@tool(context=True)
def file_operations(
    operation: str,
    paths: list = None,
    content: list = None,
    tool_context: ToolContext = None,
) -> str:
    """Manage files in the Code Interpreter sandbox.

    Args:
        operation: One of "read", "write", "list", "remove".
        paths: File paths (required for read/remove/list).
              - read:   ["file1.txt", "file2.csv"]
              - remove: ["old_file.txt"]
              - list:   ["."] or ["/path/to/dir"]  (single path)
        content: File content entries (required for write).
                Each entry: {"path": "output.txt", "text": "file content here"}

    Returns:
        Operation result (file content, file list, or confirmation).
    """
    ci = _get_ci_from_context(tool_context)
    if ci is None:
        return json.dumps({
            "error": "Code Interpreter not available. Deploy AgentCore Runtime Stack.",
            "status": "error",
        })

    try:
        if operation == "read":
            if not paths:
                return json.dumps({"error": "paths required for read operation", "status": "error"})
            resolved_paths = (
                [_mounted_path(path) for path in paths]
                if _uses_mounted_workspace(tool_context)
                else paths
            )
            download_response = ci.invoke("readFiles", {"paths": resolved_paths})
            parts = []
            for event in download_response.get("stream", []):
                result = event.get("result", {})
                for item in result.get("content", []):
                    if not isinstance(item, dict):
                        continue
                    text = item.get("text", "")
                    if text:
                        parts.append(text)
            return "\n".join(parts) if parts else "(empty)"

        elif operation == "write":
            if not content:
                return json.dumps({"error": "content required for write operation", "status": "error"})
            results = []
            for entry in content:
                path = (
                    _mounted_path(entry["path"])
                    if _uses_mounted_workspace(tool_context)
                    else entry["path"]
                )
                code = (
                    f"with open({path!r}, 'w') as _f:\n"
                    f"    _f.write({entry['text']!r})\n"
                    f"print('Written: {path}')\n"
                )
                response = ci.invoke("executeCode", {"code": code, "language": "python", "clearContext": False})
                stdout, stderr, has_error = _parse_stream(response)
                if has_error:
                    results.append(f"Error writing {path}: {stderr}")
                else:
                    results.append(stdout or f"Written: {path}")
            return "\n".join(results)

        elif operation == "list":
            list_path = paths[0] if paths else "."
            if _uses_mounted_workspace(tool_context):
                list_path = _mounted_path(list_path)
            code = (
                "import os, json\n"
                f"_p = {list_path!r}\n"
                "_entries = []\n"
                "for _n in sorted(os.listdir(_p)):\n"
                "    _full = os.path.join(_p, _n)\n"
                "    _entries.append({'name': _n, 'type': 'directory' if os.path.isdir(_full) else 'file', 'size': 0 if os.path.isdir(_full) else os.path.getsize(_full)})\n"
                "print(json.dumps(_entries, indent=2))\n"
            )
            response = ci.invoke("executeCode", {"code": code, "language": "python", "clearContext": False})
            stdout, stderr, has_error = _parse_stream(response)
            if has_error:
                return json.dumps({"error": stderr, "status": "error"})
            return stdout or "[]"

        elif operation == "remove":
            if not paths:
                return json.dumps({"error": "paths required for remove operation", "status": "error"})
            resolved_paths = (
                [_mounted_path(path) for path in paths]
                if _uses_mounted_workspace(tool_context)
                else paths
            )
            escaped = json.dumps(resolved_paths)
            code = (
                f"import os\n"
                f"for _p in {escaped}:\n"
                f"    os.remove(_p)\n"
                f"    print(f'Removed: {{_p}}')\n"
            )
            response = ci.invoke("executeCode", {"code": code, "language": "python", "clearContext": False})
            stdout, stderr, has_error = _parse_stream(response)
            if has_error:
                return json.dumps({"error": stderr, "status": "error"})
            return stdout or "Done"

        else:
            return json.dumps({
                "error": f"Unknown operation: '{operation}'. Use: read, write, list, remove",
                "status": "error",
            })

    except Exception as e:
        logger.error(f"file_operations ({operation}) error: {e}")
        return json.dumps({"error": str(e), "status": "error"})


# -----------------------------------------------------------------------
# Tool 4: ci_push_to_workspace
# -----------------------------------------------------------------------

@tool(context=True)
def ci_push_to_workspace(
    paths: list = None,
    tool_context: ToolContext = None,
) -> str:
    """Persist files for legacy Code Interpreter deployments.

    Mounted workspace deployments persist files automatically. In that mode,
    this helper only verifies that the requested files exist. It copies files
    to S3 only when the session is using the legacy sandbox configuration.

    Args:
        paths: Sandbox file paths to save (e.g. ["chart.png", "results.json"]).
               If omitted, all files in the sandbox root are saved.

    Returns:
        JSON with the list of saved files and their workspace paths.
    """
    ci = _get_ci_from_context(tool_context)
    if ci is None:
        return json.dumps({"error": "Code Interpreter not available.", "status": "error"})

    try:
        if _uses_mounted_workspace(tool_context):
            if not paths:
                code = (
                    "import os, json\n"
                    "_root = os.getcwd()\n"
                    "print(json.dumps([name for name in os.listdir(_root) "
                    "if os.path.isfile(os.path.join(_root, name))]))\n"
                )
                response = ci.invoke("executeCode", {
                    "code": code,
                    "language": "python",
                    "clearContext": False,
                })
                stdout, _, _ = _parse_stream(response)
                paths = json.loads(stdout.strip()) if stdout.strip() else []

            saved = []
            for path in paths or []:
                resolved = _mounted_path(path)
                code = (
                    "import os\n"
                    f"_p = {resolved!r}\n"
                    "print('ok' if os.path.isfile(_p) else 'missing')\n"
                )
                response = ci.invoke("executeCode", {
                    "code": code,
                    "language": "python",
                    "clearContext": False,
                })
                stdout, _, has_error = _parse_stream(response)
                if not has_error and stdout.strip() == "ok":
                    relative = os.path.relpath(resolved, os.getenv(
                        "S3_FILES_MOUNT_PATH",
                        "/mnt/workspace",
                    ))
                    saved.append(f"code-interpreter/{relative}")

            return json.dumps({
                "files_saved": saved,
                "count": len(saved),
                "status": "ok",
                "storage": "mounted",
            })

        # Discover files if no paths given
        if not paths:
            code = "import os, json; print(json.dumps([f for f in os.listdir('.') if os.path.isfile(f)]))\n"
            response = ci.invoke("executeCode", {"code": code, "language": "python", "clearContext": False})
            stdout, _, _ = _parse_stream(response)
            try:
                paths = json.loads(stdout.strip()) if stdout.strip() else []
            except Exception:
                paths = []
            if not paths:
                return json.dumps({"files_saved": [], "count": 0, "status": "ok"})

        saved = []
        for path in paths:
            try:
                download_response = ci.invoke("readFiles", {"paths": [path]})
                filename = os.path.basename(path)
                for event in download_response.get("stream", []):
                    result = event.get("result", {})
                    for item in result.get("content", []):
                        if not isinstance(item, dict):
                            continue
                        blob = item.get("data") or item.get("resource", {}).get("blob")
                        if blob:
                            info = _save_to_workspace(tool_context, filename, blob)
                            doc_type = info.get('doc_type', 'code-output') if info else 'code-output'
                            saved.append(f"{doc_type}/{filename}")
                            break
                        text = item.get("text", "")
                        if text:
                            info = _save_to_workspace(tool_context, filename, text.encode("utf-8"))
                            doc_type = info.get('doc_type', 'code-output') if info else 'code-output'
                            saved.append(f"{doc_type}/{filename}")
                            break
            except Exception as e:
                logger.warning(f"ci_push: could not save '{path}': {e}")

        return json.dumps({"files_saved": saved, "count": len(saved), "status": "ok"})

    except Exception as e:
        logger.error(f"ci_push_to_workspace error: {e}")
        return json.dumps({"error": str(e), "status": "error"})


# --- Skill registration ---
register_skill("code-interpreter", tools=[
    execute_code, execute_command, file_operations,
    ci_push_to_workspace,
])
