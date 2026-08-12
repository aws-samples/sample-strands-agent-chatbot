"""
File Processor Module

Handles file sanitization, workspace storage, and Code Interpreter integration.
Extracted from ChatbotAgent for better modularity and reusability.

Usage:
    from agent.processor import sanitize_filename, auto_store_files

    # Sanitize filename for AWS Bedrock
    safe_name = sanitize_filename("my file (1).docx")

    # Auto-store uploaded files to workspace
    auto_store_files(uploaded_files, user_id, session_id)
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional

from agent.config.constants import (
    DEFAULT_AWS_REGION,
    OFFICE_EXTENSIONS,
    IMAGE_EXTENSIONS,
    EnvVars,
)

logger = logging.getLogger(__name__)


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to meet AWS Bedrock requirements.

    Rules:
    - Only alphanumeric, hyphens, parentheses, and square brackets
    - Convert underscores and spaces to hyphens for consistency
    - No consecutive hyphens

    Args:
        filename: Original filename

    Returns:
        Sanitized filename safe for Bedrock APIs

    Example:
        >>> sanitize_filename("my_file (1).docx")
        "my-file(1).docx"
    """
    # First, replace underscores and spaces with hyphens
    sanitized = filename.replace('_', '-').replace(' ', '-')

    # Keep only allowed characters: alphanumeric, hyphens, parentheses, square brackets
    sanitized = re.sub(r'[^a-zA-Z0-9\-\(\)\[\]]', '', sanitized)

    # Replace consecutive hyphens with single hyphen
    sanitized = re.sub(r'\-+', '-', sanitized)

    # Trim hyphens from start/end
    sanitized = sanitized.strip('-')

    # If name becomes empty, use default
    if not sanitized:
        sanitized = 'document'

    return sanitized


def sanitize_full_filename(filename: str) -> str:
    """
    Sanitize full filename while preserving extension.

    Args:
        filename: Original filename with extension

    Returns:
        Sanitized filename with original extension

    Example:
        >>> sanitize_full_filename("my_file (1).docx")
        "my-file(1).docx"
    """
    if '.' in filename:
        name_parts = filename.rsplit('.', 1)
        return sanitize_filename(name_parts[0]) + '.' + name_parts[1]
    return sanitize_filename(filename)


def get_code_interpreter_id() -> Optional[str]:
    """
    Get Code Interpreter ID from environment or Parameter Store.

    Checks in order:
    1. CODE_INTERPRETER_ID environment variable
    2. SSM Parameter Store: /{PROJECT_NAME}/{ENVIRONMENT}/agentcore/code-interpreter-id

    Returns:
        Code Interpreter ID string, or None if not found
    """
    # Check environment variable first
    code_interpreter_id = os.getenv(EnvVars.CODE_INTERPRETER_ID)
    if code_interpreter_id:
        logger.debug(f"Found CODE_INTERPRETER_ID in environment: {code_interpreter_id}")
        return code_interpreter_id

    # Try Parameter Store
    try:
        import boto3
        project_name = os.getenv(EnvVars.PROJECT_NAME, 'strands-agent-chatbot')
        environment = os.getenv(EnvVars.ENVIRONMENT, 'dev')
        region = os.getenv(EnvVars.AWS_REGION, DEFAULT_AWS_REGION)
        param_name = f"/{project_name}/{environment}/agentcore/code-interpreter-id"

        logger.debug(f"Checking Parameter Store for Code Interpreter ID: {param_name}")
        ssm = boto3.client('ssm', region_name=region)
        response = ssm.get_parameter(Name=param_name)
        code_interpreter_id = response['Parameter']['Value']
        logger.debug(f"Found CODE_INTERPRETER_ID in Parameter Store: {code_interpreter_id}")
        return code_interpreter_id
    except Exception as e:
        logger.warning(f"CODE_INTERPRETER_ID not found in env or Parameter Store: {e}")
        return None


def get_workspace_context(user_id: str, session_id: str) -> Optional[str]:
    """
    Get workspace file list as context string.

    Args:
        user_id: User identifier
        session_id: Session identifier

    Returns:
        Context string listing workspace files, or None if empty/error
    """
    try:
        from workspace import WordManager
        doc_manager = WordManager(user_id, session_id)
        documents = doc_manager.list_s3_documents()

        if documents:
            files_list = ", ".join([
                f"{doc['filename']} ({doc['size_kb']})"
                for doc in documents
            ])
            return f"[Word documents in your workspace: {files_list}]"
        return None
    except Exception as e:
        logger.debug(f"Failed to get workspace context: {e}")
        return None


def store_files_by_type(
    uploaded_files: List[Dict[str, Any]],
    code_interpreter: Any,
    extensions: List[str],
    manager_class: type,
    document_type: str,
    user_id: str,
    session_id: str,
) -> None:
    """
    Store files of specific type to workspace.

    Args:
        uploaded_files: List of uploaded file info dicts
        code_interpreter: Active CodeInterpreter instance
        extensions: List of file extensions to filter (e.g., ['.docx'])
        manager_class: DocumentManager class (e.g., WordDocumentManager)
        document_type: Type name for logging (e.g., 'Word', 'Excel', 'image')
        user_id: User identifier
        session_id: Session identifier
    """
    # Debug: log what we're filtering
    logger.debug(f"Filtering {len(uploaded_files)} files for {document_type} (extensions: {extensions})")
    for f in uploaded_files:
        logger.debug(f"   - {f['filename']} (matches: {any(f['filename'].lower().endswith(ext) for ext in extensions)})")

    # Filter files by extensions
    filtered_files = [
        f for f in uploaded_files
        if any(f['filename'].lower().endswith(ext) for ext in extensions)
    ]

    logger.debug(f"Filtered {len(filtered_files)} {document_type} file(s)")

    if not filtered_files:
        return

    # Initialize document manager
    doc_manager = manager_class(user_id, session_id)

    # Store each file
    for file_info in filtered_files:
        try:
            filename = file_info['filename']
            file_bytes = file_info['bytes']

            # Sync to both S3 and Code Interpreter
            doc_manager.sync_to_both(
                code_interpreter,
                filename,
                file_bytes,
                metadata={'auto_stored': 'true'}
            )
            logger.debug(f"Auto-stored {document_type}: {filename}")
        except Exception as e:
            logger.error(f"Failed to auto-store {document_type} file {filename}: {e}")


def auto_store_files(
    uploaded_files: List[Dict[str, Any]],
    user_id: str,
    session_id: str,
) -> None:
    """
    Store uploaded files to the durable S3 workspace.

    S3 is the single source of truth — all workspace tools (word, excel, etc.) read
    from S3. When S3 Files is enabled, a second object is written into the mounted
    Code Interpreter input directory and imported on first filesystem access.

    Args:
        uploaded_files: List of dicts with 'filename', 'bytes', 'content_type'
        user_id: User identifier
        session_id: Session identifier
    """
    if not uploaded_files:
        return

    logger.debug(f"Auto-store called with {len(uploaded_files)} file(s):")
    for f in uploaded_files:
        logger.debug(f"   - {f['filename']} ({f.get('content_type', 'unknown')})")

    try:
        from workspace import (
            WordManager,
            ExcelManager,
            PowerPointManager,
            ImageManager,
            ZipManager,
        )
        from workspace.base_manager import BaseDocumentManager

        file_type_configs = [
            {'extensions': OFFICE_EXTENSIONS['word'],        'manager_class': WordManager},
            {'extensions': OFFICE_EXTENSIONS['excel'],       'manager_class': ExcelManager},
            {'extensions': OFFICE_EXTENSIONS['powerpoint'],  'manager_class': PowerPointManager},
            {'extensions': ['.zip'],                          'manager_class': ZipManager},
            {'extensions': list(IMAGE_EXTENSIONS),            'manager_class': ImageManager},
        ]
        known_extensions = {ext for c in file_type_configs for ext in c['extensions']}
        if os.getenv("S3_FILES_FILE_SYSTEM_ID"):
            # Structured data is durably stored in the mounted workspace input
            # prefix below, so avoid a second copy in documents/raw.
            known_extensions.update({'.json', '.jsonl', '.ndjson'})

        # Step 1: Always save to S3 (no CI required)
        for config in file_type_configs:
            filtered = [
                f for f in uploaded_files
                if any(f['filename'].lower().endswith(ext) for ext in config['extensions'])
            ]
            if not filtered:
                continue
            manager = config['manager_class'](user_id, session_id)
            for file_info in filtered:
                try:
                    manager.save_to_s3(file_info['filename'], file_info['bytes'], metadata={'auto_stored': 'true'})
                    logger.debug(f"Saved to S3: {file_info['filename']}")
                except Exception as e:
                    logger.error(f"Failed to save {file_info['filename']} to S3: {e}")

        # Fallback: unknown types → raw namespace
        for file_info in uploaded_files:
            if not any(file_info['filename'].lower().endswith(ext) for ext in known_extensions):
                try:
                    mgr = BaseDocumentManager(user_id, session_id, document_type='raw')
                    mgr.save_to_s3(file_info['filename'], file_info['bytes'], metadata={'auto_stored': 'true'})
                    logger.debug(f"Saved to S3 (raw): {file_info['filename']}")
                except Exception as e:
                    logger.error(f"Failed to save {file_info['filename']} to S3: {e}")

        if os.getenv("S3_FILES_FILE_SYSTEM_ID"):
            import boto3

            from workspace.config import get_workspace_bucket
            from workspace.paths import code_interpreter_prefix

            s3 = boto3.client(
                "s3",
                region_name=os.getenv(EnvVars.AWS_REGION, DEFAULT_AWS_REGION),
            )
            bucket = get_workspace_bucket()
            for file_info in uploaded_files:
                key = (
                    f"{code_interpreter_prefix(user_id, session_id)}"
                    f"inputs/{file_info['filename']}"
                )
                try:
                    s3.put_object(
                        Bucket=bucket,
                        Key=key,
                        Body=file_info["bytes"],
                        ContentType=file_info.get(
                            "content_type",
                            "application/octet-stream",
                        ),
                        Metadata={
                            "user_id": user_id,
                            "session_id": session_id,
                            "source": "chat_upload",
                        },
                    )
                    logger.debug("Saved mounted CI input: %s", key)
                except Exception as error:
                    logger.error(
                        "Failed to save mounted CI input %s: %s",
                        file_info["filename"],
                        error,
                    )

    except Exception as e:
        logger.error(f"Failed to auto-store files: {e}")
