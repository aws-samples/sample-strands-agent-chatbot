"""General-purpose Code Interpreter tools using AWS Bedrock AgentCore Code Interpreter.

Provides 3 tools wrapping CodeInterpreter (bedrock_agentcore):
  - execute_code:    Run Python/JS/TS code
  - execute_command: Run shell commands
  - file_operations: Read/write/list/remove files in the mounted workspace

All tools share a single persistent CI session per user/session — files and
variables persist across tool calls. Word, Excel, and other document tools
use the same session via get_ci_session().

Session ID is cached in agent.state so it survives across turns without
creating new sessions. Every session requires the session-scoped S3 Files
workspace mounted at /mnt/workspace.
"""

from strands import tool, ToolContext
from skill import register_skill
from typing import Any, Dict, Optional
import json
import logging
import os

logger = logging.getLogger(__name__)

# In-process CI client cache: session_key → CodeInterpreter
# This avoids re-creating CodeInterpreter objects within the same process.
# The actual session_id is stored in agent.state for cross-turn persistence.
_ci_clients: Dict[str, Any] = {}

# agent.state keys for CI session persistence
_STATE_CI_SESSION_ID = "ci_session_id"
_STATE_CI_IDENTIFIER = "ci_identifier"
_STATE_CI_MOUNTED_WORKSPACE = "ci_mounted_workspace"

# Session timeout in seconds (1 hour; max is 28800 = 8 hours)
_SESSION_TIMEOUT_SECONDS = 3600


def invalidate_session(user_id: str, session_id: str) -> None:
    """Remove cached CI client so next tool call creates a fresh session.

    Called by file_processor.auto_store_files() after uploading new files,
    so the next tool call will create a new mounted session.
    """
    session_key = f"{user_id}-{session_id}"
    if session_key in _ci_clients:
        try:
            _ci_clients[session_key].stop()
        except Exception:
            pass
        del _ci_clients[session_key]
        logger.info(f"Invalidated CI client cache: {session_key}")



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


def get_ci_session(tool_context: ToolContext) -> Any:
    """Get or create a shared CodeInterpreter session for all tools.

    Session lifecycle:
    1. Check in-process cache (_ci_clients) for existing client
    2. Check agent.state for stored session_id (cross-turn persistence)
    3. Verify session is still READY via get_session() API
    4. If expired/missing, create a new session with the S3 Files mount
    5. Store new session_id in agent.state

    All tools (execute_code, word, excel, powerpoint, etc.) call this to get
    the same persistent sandbox. Never call .start() or .stop() on the result.

    Args:
        tool_context: Strands ToolContext (provides agent.state + invocation_state)

    Returns:
        CodeInterpreter instance with the session workspace mounted.

    Raises:
        RuntimeError: If Code Interpreter or the S3 Files workspace is not
            configured, or the mounted session cannot be started.
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
        if tool_context.agent.state.get(_STATE_CI_MOUNTED_WORKSPACE) is not True:
            logger.info("Discarding non-mounted cached CI session: %s", session_key)
            try:
                ci.stop()
            except Exception:
                pass
            del _ci_clients[session_key]
            ci = None

    if ci is not None:
        # Verify the cached client's session is still alive
        stored_id = ci.session_id
        stored_identifier = ci.identifier
        if stored_id and stored_identifier and _is_session_alive(ci, stored_identifier, stored_id):
            try:
                _prepare_mounted_workspace(ci)
                return ci
            except Exception as error:
                logger.warning(
                    "Cached CI session workspace is unavailable; recreating: %s",
                    error,
                )
                try:
                    ci.stop()
                except Exception:
                    pass
        # Session expired — remove stale cache
        logger.info(f"Cached CI session expired: {session_key}")
        del _ci_clients[session_key]

    # Step 2: Try reattaching from agent.state (cross-turn persistence)
    agent_state = tool_context.agent.state
    stored_session_id = agent_state.get(_STATE_CI_SESSION_ID)
    stored_identifier = agent_state.get(_STATE_CI_IDENTIFIER)

    mounted_session = agent_state.get(_STATE_CI_MOUNTED_WORKSPACE) is True
    if stored_session_id and stored_identifier and mounted_session:
        ci = CodeInterpreter(region)
        ci.identifier = stored_identifier
        ci.session_id = stored_session_id
        if _is_session_alive(ci, stored_identifier, stored_session_id):
            try:
                _prepare_mounted_workspace(ci)
                logger.info(f"Reattached to existing CI session: {stored_session_id}")
                _ci_clients[session_key] = ci
                return ci
            except Exception as error:
                logger.warning(
                    "Stored CI session workspace is unavailable; recreating: %s",
                    error,
                )
                try:
                    ci.stop()
                except Exception:
                    pass
        logger.info(f"Stored CI session expired ({stored_session_id}), creating new one")
    elif stored_session_id:
        logger.info(
            "Ignoring stored CI session without a mounted workspace: %s",
            stored_session_id,
        )

    # Step 3: Create a new session
    identifier = stored_identifier or _get_code_interpreter_id()
    if not identifier:
        raise RuntimeError(
            "Code Interpreter is not configured: CODE_INTERPRETER_ID is missing"
        )

    ci = CodeInterpreter(region)
    from workspace.s3_files import get_or_create_session_access_point

    workspace = get_or_create_session_access_point(
        agent_state,
        user_id,
        session_id,
    )
    if not workspace:
        raise RuntimeError(
            "Code Interpreter workspace is not configured: "
            "S3_FILES_FILE_SYSTEM_ID and S3_FILES_FILE_SYSTEM_ARN are required"
        )

    try:
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
    except Exception as error:
        raise RuntimeError(
            f"Could not start Code Interpreter with the S3 Files workspace: {error}"
        ) from error

    logger.info(f"Created new CI session: {ci.session_id} (identifier: {identifier}, timeout: {_SESSION_TIMEOUT_SECONDS}s)")

    # Store in agent.state for cross-turn persistence
    agent_state.set(_STATE_CI_SESSION_ID, ci.session_id)
    agent_state.set(_STATE_CI_IDENTIFIER, identifier)
    agent_state.set(_STATE_CI_MOUNTED_WORKSPACE, True)

    # Cache in-process
    _ci_clients[session_key] = ci

    try:
        _prepare_mounted_workspace(ci)
    except Exception as error:
        _ci_clients.pop(session_key, None)
        agent_state.set(_STATE_CI_SESSION_ID, None)
        agent_state.set(_STATE_CI_MOUNTED_WORKSPACE, False)
        try:
            ci.stop()
        except Exception:
            pass
        raise RuntimeError(
            f"Could not initialize the mounted Code Interpreter workspace: {error}"
        ) from error

    return ci


def _prepare_mounted_workspace(ci: Any) -> None:
    """Set the Code Interpreter working directory and trigger input import."""
    mount_path = os.getenv("S3_FILES_MOUNT_PATH", "/mnt/workspace")
    code = (
        "import os\n"
        f"_mount = {mount_path!r}\n"
        "if not os.path.isdir(_mount):\n"
        "    raise RuntimeError(f'Mounted workspace is unavailable: {_mount}')\n"
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


def _get_ci_from_context(tool_context: ToolContext) -> Any:
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
    try:
        ci = _get_ci_from_context(tool_context)
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
        output_path = _mounted_path(output_filename)
        download_response = ci.invoke("readFiles", {"paths": [output_path]})
        for event in download_response.get("stream", []):
            result = event.get("result", {})
            for item in result.get("content", []):
                if not isinstance(item, dict):
                    continue
                blob = item.get("data") or item.get("resource", {}).get("blob")
                if blob:
                    size_kb = len(blob) / 1024
                    workspace_path = f"code-interpreter/{output_filename}"
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
    try:
        ci = _get_ci_from_context(tool_context)
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
    """Manage files in the mounted Code Interpreter workspace.

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
    try:
        ci = _get_ci_from_context(tool_context)
        if operation == "read":
            if not paths:
                return json.dumps({"error": "paths required for read operation", "status": "error"})
            resolved_paths = [_mounted_path(path) for path in paths]
            code = (
                "import json\n"
                f"_paths = {json.dumps(resolved_paths)}\n"
                "_results = []\n"
                "for _p in _paths:\n"
                "    with open(_p, 'r', encoding='utf-8', errors='replace') as _f:\n"
                "        _text = _f.read(1000001)\n"
                "    _results.append({\n"
                "        'path': _p,\n"
                "        'text': _text[:1000000],\n"
                "        'truncated': len(_text) > 1000000,\n"
                "    })\n"
                "print(json.dumps(_results))\n"
            )
            response = ci.invoke(
                "executeCode",
                {"code": code, "language": "python", "clearContext": False},
            )
            stdout, stderr, has_error = _parse_stream(response)
            if has_error:
                return json.dumps({"error": stderr, "status": "error"})
            try:
                results = json.loads(stdout)
            except json.JSONDecodeError:
                return json.dumps({
                    "error": "Code Interpreter returned an invalid read result",
                    "status": "error",
                })
            if len(results) == 1 and not results[0]["truncated"]:
                return results[0]["text"] or "(empty)"
            return json.dumps({"files": results, "status": "success"})

        elif operation == "write":
            if not content:
                return json.dumps({"error": "content required for write operation", "status": "error"})
            results = []
            for entry in content:
                path = _mounted_path(entry["path"])
                code = (
                    "import os\n"
                    f"os.makedirs(os.path.dirname({path!r}), exist_ok=True)\n"
                    f"with open({path!r}, 'w', encoding='utf-8') as _f:\n"
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
            resolved_paths = [_mounted_path(path) for path in paths]
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


# --- Skill registration ---
register_skill("code-interpreter", tools=[
    execute_code, execute_command, file_operations,
])
