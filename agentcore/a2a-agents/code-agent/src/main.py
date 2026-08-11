"""
Code Agent A2A Server

Receives coding tasks via A2A protocol and executes them autonomously
using built-in tools: Read, Write, Edit, Bash, Glob, Grep.

Authentication: CLAUDE_CODE_USE_BEDROCK=1 + IAM execution role (no API key needed)

For local testing:
    CLAUDE_CODE_USE_BEDROCK=1 ANTHROPIC_MODEL=us.anthropic.claude-sonnet-5 \
    uvicorn src.main:app --port 9000 --reload
"""

import asyncio
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater, InMemoryTaskStore
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.apps import A2AStarletteApplication
from a2a.types import AgentCard, AgentCapabilities, AgentSkill, Part, TextPart

import uvicorn
from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    SystemMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    CLINotFoundError,
    CLIConnectionError,
    ProcessError,
    CLIJSONDecodeError,
)
from .session_workspace import restore_s3_prefix, sync_session_inputs

# Claude Agent SDK cannot run inside an existing Claude Code session.
# Unset CLAUDECODE so nested invocation is allowed in all environments.
os.environ.pop("CLAUDECODE", None)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class _SuppressHealthCheck(logging.Filter):
    """Filter out noisy /ping health check access logs from uvicorn."""
    def filter(self, record: logging.LogRecord) -> bool:
        return "GET /ping" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_SuppressHealthCheck())

# ============================================================
# Configuration
# ============================================================
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
PORT = int(os.getenv("PORT", "9000"))
PROJECT_NAME = os.getenv("PROJECT_NAME", "strands-agent-chatbot")
ENVIRONMENT = os.getenv("ENVIRONMENT", "dev")
WORKSPACE_BASE = os.getenv("WORKSPACE_BASE", "/tmp/workspaces")
CODE_AGENT_MODEL_ID = os.getenv(
    "CODE_AGENT_MODEL_ID",
    os.getenv("ANTHROPIC_MODEL", "us.anthropic.claude-sonnet-5"),
)
# Claude Code reads this variable when its subprocess starts. Set it once at
# process startup; mutating it per request would race across concurrent users.
os.environ["ANTHROPIC_MODEL"] = CODE_AGENT_MODEL_ID


def _resolve_artifact_bucket() -> str:
    """Get artifact bucket name from env var, falling back to SSM."""
    bucket = os.getenv("ARTIFACT_BUCKET", "")
    if bucket:
        return bucket
    try:
        import boto3
        ssm = boto3.client("ssm", region_name=AWS_REGION)
        resp = ssm.get_parameter(Name=f"/{PROJECT_NAME}/{ENVIRONMENT}/agentcore/artifact-bucket")
        return resp["Parameter"]["Value"]
    except Exception as e:
        logger.warning(f"[Config] Could not resolve ARTIFACT_BUCKET from SSM: {e}")
        return ""


ARTIFACT_BUCKET = _resolve_artifact_bucket()

# ~/.claude/ — where Claude Code CLI stores session .jsonl files
CLAUDE_HOME = Path.home() / ".claude"

# Tools available to the coding agent.
# Task and Notebook* are excluded: Task spawns sub-agents (hard to control),
# Notebook tools are not needed in a coding workspace.
ALLOWED_TOOLS = [
    "Read", "Write", "Edit", "Bash", "Glob", "Grep",
    "TodoRead", "TodoWrite",
    "WebFetch", "WebSearch",
]

# Tool name → user-friendly status message for streaming
TOOL_STATUS_MAP = {
    "Read":      "Reading file",
    "Write":     "Writing file",
    "Edit":      "Editing file",
    "Bash":      "Running command",
    "Glob":      "Searching files",
    "Grep":      "Searching content",
    "TodoRead":  "Reading todos",
    "TodoWrite": "Writing todos",
    "WebSearch": "Searching web",
    "WebFetch":  "Fetching URL",
}

# In-memory map: "{user_id}-{session_id}" → claude_agent_sdk session_id
# Allows resuming the same Claude Agent session across multiple A2A calls
_sdk_sessions: dict = {}

# In-memory map: "{user_id}-{session_id}" → ClaudeSDKClient instance
# Keeps the Claude Code subprocess alive across A2A task calls (warm start)
_sdk_clients: dict[str, ClaudeSDKClient] = {}

# In-memory map: a2a task_id → asyncio.Event
# Set by cancel() to signal execute() to stop consuming messages gracefully
_cancel_events: dict[str, asyncio.Event] = {}

# In-memory map: a2a task_id → sdk_key ("{user_id}-{session_id}")
# Used by cancel() to find the client instance for interrupt()
_task_to_sdk_key: dict[str, str] = {}



# ============================================================
# Session Persistence (S3 sync/restore)
# ============================================================

def _s3():
    import boto3
    return boto3.client("s3", region_name=AWS_REGION)


def _workspace_s3_prefix(user_id: str, session_id: str) -> str:
    return f"code-agent-workspace/{user_id}/{session_id}"


def _claude_home_s3_prefix(user_id: str, session_id: str) -> str:
    return f"code-agent-sessions/{user_id}/{session_id}/claude-home"


def _sdk_session_id_s3_key(user_id: str, session_id: str) -> str:
    return f"code-agent-sessions/{user_id}/{session_id}/sdk_session_id"


# Directories excluded from S3 sync/restore (build artifacts, dependency caches)
_SYNC_EXCLUDE_DIRS = {
    "node_modules", ".next", ".nuxt", ".svelte-kit",  # JS/TS
    ".venv", "venv", "env", ".env",                   # Python virtualenvs
    "__pycache__", ".mypy_cache", ".pytest_cache",    # Python caches
    "dist", "build", "out", "target",                 # Build outputs
    ".gradle", ".m2",                                 # Java/Kotlin
    ".cache", ".parcel-cache", ".turbo",              # General caches
    ".git",                                           # Git internals
}


def _should_exclude(rel: Path) -> bool:
    """Return True if any path component is in the exclude list."""
    return any(part in _SYNC_EXCLUDE_DIRS for part in rel.parts)


def _sync_dir_to_s3(
    local_dir: Path,
    bucket: str,
    s3_prefix: str,
    s3_client,
    *,
    exclude_top_level_inputs: bool = False,
) -> int:
    """Upload all files under local_dir to s3://bucket/s3_prefix/. Returns upload count."""
    uploaded = 0
    for file_path in local_dir.rglob("*"):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(local_dir)
        if exclude_top_level_inputs and rel.parts[0] == "inputs":
            continue
        if _should_exclude(rel):
            continue
        s3_key = f"{s3_prefix}/{rel}"
        try:
            s3_client.upload_file(str(file_path), bucket, s3_key)
            uploaded += 1
        except Exception as e:
            logger.warning(f"[S3 sync] Failed to upload {file_path}: {e}")
    return uploaded


def restore_session(user_id: str, session_id: str, workspace: Path) -> Optional[str]:
    """Restore workspace + ~/.claude/ from S3. Returns sdk_session_id if previously saved."""
    if not ARTIFACT_BUCKET:
        return None

    s3 = _s3()

    # 1. Restore workspace files
    n = restore_s3_prefix(
        workspace,
        ARTIFACT_BUCKET,
        _workspace_s3_prefix(user_id, session_id),
        s3,
        exclude_top_level_inputs=True,
        excluded_parts=_SYNC_EXCLUDE_DIRS,
    )
    if n:
        logger.info(f"[S3 restore] Workspace: {n} files")

    # 2. Restore ~/.claude/ (contains session .jsonl for resume=)
    n = restore_s3_prefix(
        CLAUDE_HOME,
        ARTIFACT_BUCKET,
        _claude_home_s3_prefix(user_id, session_id),
        s3,
        excluded_parts=_SYNC_EXCLUDE_DIRS,
    )
    if n:
        logger.info(f"[S3 restore] Claude home: {n} files")

    # 3. Retrieve sdk_session_id saved from previous run
    try:
        resp = s3.get_object(Bucket=ARTIFACT_BUCKET, Key=_sdk_session_id_s3_key(user_id, session_id))
        sdk_session_id = resp["Body"].read().decode("utf-8").strip()
        logger.info(f"[S3 restore] sdk_session_id: {sdk_session_id}")
        return sdk_session_id
    except s3.exceptions.NoSuchKey:
        return None
    except Exception as e:
        logger.warning(f"[S3 restore] Could not retrieve sdk_session_id: {e}")
        return None


def sync_session(user_id: str, session_id: str, workspace: Path, sdk_session_id: Optional[str]) -> None:
    """Sync workspace + ~/.claude/ to S3 after task completion."""
    if not ARTIFACT_BUCKET:
        return

    s3 = _s3()

    # 1. Sync workspace files
    n = _sync_dir_to_s3(
        workspace,
        ARTIFACT_BUCKET,
        _workspace_s3_prefix(user_id, session_id),
        s3,
        exclude_top_level_inputs=True,
    )
    logger.info(f"[S3 sync] Workspace: {n} files")

    # 2. Sync ~/.claude/ so session .jsonl survives container restarts
    if CLAUDE_HOME.exists():
        n = _sync_dir_to_s3(CLAUDE_HOME, ARTIFACT_BUCKET, _claude_home_s3_prefix(user_id, session_id), s3)
        logger.info(f"[S3 sync] Claude home: {n} files")

    # 3. Save sdk_session_id for next run
    if sdk_session_id:
        try:
            s3.put_object(
                Bucket=ARTIFACT_BUCKET,
                Key=_sdk_session_id_s3_key(user_id, session_id),
                Body=sdk_session_id.encode("utf-8"),
                ContentType="text/plain",
            )
            logger.info("[S3 sync] sdk_session_id saved")
        except Exception as e:
            logger.warning(f"[S3 sync] Failed to save sdk_session_id: {e}")


def _clear_session_history(user_id: str, session_id: str) -> None:
    """Delete conversation history and sdk_session_id from S3.

    Called on reset_session=True. Workspace files are intentionally preserved —
    the user may want to start a new conversation about the same codebase.
    Also clears ~/.claude/ locally so no stale session files remain.
    """
    if not ARTIFACT_BUCKET:
        return

    s3 = _s3()

    # Delete Claude home snapshot (conversation .jsonl files)
    claude_prefix = _claude_home_s3_prefix(user_id, session_id)
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=ARTIFACT_BUCKET, Prefix=claude_prefix + "/"):
            objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objects:
                s3.delete_objects(Bucket=ARTIFACT_BUCKET, Delete={"Objects": objects})
        logger.info("[S3 reset] Cleared Claude session history")
    except Exception as e:
        logger.warning(f"[S3 reset] Could not clear Claude session history: {e}")

    # Delete stored sdk_session_id
    try:
        s3.delete_object(Bucket=ARTIFACT_BUCKET, Key=_sdk_session_id_s3_key(user_id, session_id))
    except Exception as e:
        logger.warning(f"[S3 reset] Could not delete sdk_session_id: {e}")

    # Clear session .jsonl files from ~/.claude/
    if CLAUDE_HOME.exists():
        for item in CLAUDE_HOME.iterdir():
            if item.is_dir():
                import shutil  # noqa: PLC0415
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        logger.info("[S3 reset] Cleared local ~/.claude/")



def build_task_with_files(task_text: str, file_descriptions: list[str]) -> str:
    """Prepend canonical session input paths to the delegated task."""
    if not file_descriptions:
        return task_text

    files_block = "\n".join(file_descriptions)
    return (
        f"The following session files are synchronized under `inputs/`:\n"
        f"{files_block}\n\n"
        f"{task_text}"
    )


# ============================================================
# Client Lifecycle Helpers
# ============================================================

def _build_client_options(
    sdk_session_id: Optional[str] = None,
    workspace: Optional[Path] = None,
    max_turns: int = 100,
) -> ClaudeAgentOptions:
    """Build ClaudeAgentOptions with common settings."""
    return ClaudeAgentOptions(
        allowed_tools=ALLOWED_TOOLS,
        resume=sdk_session_id,
        permission_mode="bypassPermissions",
        cwd=str(workspace) if workspace else None,
        system_prompt={"type": "preset", "preset": "claude_code"},
        setting_sources=["user", "project"],
        max_turns=max_turns,
    )


async def _get_or_create_client(
    sdk_key: str,
    options: ClaudeAgentOptions,
) -> ClaudeSDKClient:
    """Get an existing connected client or create a new one.

    The client keeps the Claude Code subprocess alive across A2A task calls,
    enabling warm starts and graceful interrupt via client.interrupt().
    """
    existing = _sdk_clients.get(sdk_key)
    if existing and existing._query is not None:
        logger.info(f"[Client] Reusing existing client for {sdk_key}")
        return existing

    # Discard stale client if any
    if existing:
        logger.info(f"[Client] Discarding disconnected client for {sdk_key}")
        try:
            await existing.disconnect()
        except Exception:
            pass
        _sdk_clients.pop(sdk_key, None)

    # Create and connect new client
    client = ClaudeSDKClient(options=options)
    await client.connect()
    _sdk_clients[sdk_key] = client
    logger.info(f"[Client] Created new client for {sdk_key}")
    return client


async def _disconnect_client(sdk_key: str) -> None:
    """Disconnect and remove a cached client."""
    client = _sdk_clients.pop(sdk_key, None)
    if client:
        try:
            await client.disconnect()
            logger.info(f"[Client] Disconnected client for {sdk_key}")
        except Exception as e:
            logger.warning(f"[Client] Disconnect failed for {sdk_key}: {e}")


# ============================================================
# A2A Executor
# ============================================================

class ClaudeCodeExecutor(AgentExecutor):
    """
    A2A Executor that wraps Claude Agent SDK in streaming mode.

    Uses ClaudeSDKClient for long-lived subprocess connections. This enables:
    - Graceful interrupt via client.interrupt() instead of SIGTERM
    - Session continuity: the subprocess stays alive across A2A task calls
    - Warm starts: no subprocess restart between tasks in the same session

    Tool usage events are streamed back as intermediate A2A artifacts.
    The final result is emitted as the "code_result" artifact.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        cancel_event = asyncio.Event()
        _cancel_events[context.task_id] = cancel_event
        try:
            await self._execute_impl(context, event_queue, updater, cancel_event)
        except asyncio.CancelledError:
            # Safety net: framework calls producer_task.cancel() after cancel().
            # S3 sync should already be done in _execute_impl's finally block.
            logger.info(f"[ClaudeCodeExecutor] Task {context.task_id} CancelledError (fallback)")
            raise
        finally:
            _cancel_events.pop(context.task_id, None)
            _task_to_sdk_key.pop(context.task_id, None)

    async def _execute_impl(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        updater: TaskUpdater,
        cancel_event: asyncio.Event,
    ) -> None:
        # --- Extract task text ---
        task_text = _extract_text(context)
        if not task_text:
            await updater.add_artifact(
                [Part(root=TextPart(text="Error: No task provided"))],
                name="error"
            )
            await updater.complete()
            return

        # --- Extract metadata (session_id, user_id passed by orchestrator) ---
        metadata = _extract_metadata(context)
        session_id = metadata.get("session_id", str(uuid.uuid4()))
        user_id = metadata.get("user_id", "default_user")

        orchestrator_model_id = metadata.get("orchestrator_model_id")
        logger.info(
            "[ClaudeCodeExecutor] session=%s, user=%s, model=%s, orchestrator_model=%s",
            session_id,
            user_id,
            CODE_AGENT_MODEL_ID,
            orchestrator_model_id,
        )
        logger.info(f"[ClaudeCodeExecutor] task={task_text[:200]}")

        # --- Per-session workspace directory ---
        workspace = Path(WORKSPACE_BASE) / user_id / session_id
        workspace.mkdir(parents=True, exist_ok=True)

        # --- Inject workspace CLAUDE.md if not present ---
        claude_md = workspace / "CLAUDE.md"
        if not claude_md.exists():
            claude_md.write_text(
                "# Agent Context\n\n"
                "You are being orchestrated by an AI agent (not a human directly). "
                "Your task descriptions come from the orchestrating agent, which communicates with the human user.\n\n"
                "## Guidelines\n\n"
                "- Focus on completing the task efficiently and correctly.\n"
                "- If you encounter a genuine ambiguity that the codebase alone cannot resolve "
                "(e.g., a design choice between two valid approaches, unclear requirements), "
                "state the question clearly in your response — the orchestrator will resolve it.\n"
                "- Do NOT ask for confirmation on implementation details you can figure out yourself.\n"
                "- Keep your final response concise: summarize what you did, what files changed, "
                "and any issues or decisions worth noting.\n"
                "- Treat files under `inputs/` as read-only session attachments. "
                "Write generated or modified files outside `inputs/`.\n"
            )

        # --- Session management ---
        sdk_key = f"{user_id}-{session_id}"
        _task_to_sdk_key[context.task_id] = sdk_key
        reset_session = metadata.get("reset_session", False)
        compact_session = metadata.get("compact_session", False)

        if reset_session:
            # Disconnect existing client + clear S3 conversation data (keep workspace files)
            await _disconnect_client(sdk_key)
            _sdk_sessions.pop(sdk_key, None)
            _clear_session_history(user_id, session_id)
            sdk_session_id = None
            logger.info("[ClaudeCodeExecutor] Session reset — starting fresh")
        else:
            sdk_session_id = _sdk_sessions.get(sdk_key)
            if not sdk_session_id:
                # Not in memory — container may have restarted; try to restore from S3
                sdk_session_id = restore_session(user_id, session_id, workspace)
                if sdk_session_id:
                    _sdk_sessions[sdk_key] = sdk_session_id
                    logger.info(f"[ClaudeCodeExecutor] Session restored from S3: {sdk_session_id}")
                else:
                    logger.info("[ClaudeCodeExecutor] Starting new SDK session")
            else:
                logger.info(f"[ClaudeCodeExecutor] Resuming SDK session (in-memory): {sdk_session_id}")

        # --- Sync canonical session uploads into workspace/inputs ---
        file_descriptions = (
            sync_session_inputs(
                ARTIFACT_BUCKET,
                user_id,
                session_id,
                workspace,
                _s3(),
            )
            if ARTIFACT_BUCKET
            else []
        )
        task_text = build_task_with_files(task_text, file_descriptions)

        await updater.submit()

        step_counter = 0
        todo_counter = 0
        final_result = None
        files_changed: set = set()   # paths written/edited during this task
        last_todos: list = []        # most recent TodoWrite state

        # --- Get or create streaming client ---
        options = _build_client_options(sdk_session_id, workspace)
        try:
            client = await _get_or_create_client(sdk_key, options)
        except CLINotFoundError as e:
            logger.exception("[ClaudeCodeExecutor] Claude CLI not found — check container setup")
            await updater.add_artifact(
                [Part(root=TextPart(text=f"Error: Claude CLI not found. Check container setup. ({e})"))],
                name="error"
            )
            await updater.failed()
            return

        # --- Compact conversation history before running the task (if requested) ---
        if compact_session and sdk_session_id:
            logger.info("[ClaudeCodeExecutor] Compacting conversation history…")
            try:
                await client.query(prompt="/compact")
                async for msg in client.receive_response():
                    if isinstance(msg, SystemMessage) and msg.subtype == "init":
                        new_sid = msg.data.get("session_id")
                        if new_sid:
                            sdk_session_id = new_sid
                            _sdk_sessions[sdk_key] = new_sid
                    elif isinstance(msg, ResultMessage):
                        if msg.session_id:
                            sdk_session_id = msg.session_id
                            _sdk_sessions[sdk_key] = msg.session_id
                logger.info(f"[ClaudeCodeExecutor] Compaction done — session: {sdk_session_id}")
            except Exception as e:
                logger.warning(f"[ClaudeCodeExecutor] Compaction failed (proceeding anyway): {e}")

        # --- Execute main query ---
        try:
            await client.query(prompt=task_text)

            async for message in client.receive_messages():
                # Check cancel event — graceful exit on interrupt
                if cancel_event.is_set():
                    logger.info("[ClaudeCodeExecutor] Cancel event detected, exiting message loop")
                    break

                # Capture SDK session_id from init event (for future resume)
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    new_sid = message.data.get("session_id")
                    if new_sid and new_sid != sdk_session_id:
                        sdk_session_id = new_sid
                        _sdk_sessions[sdk_key] = new_sid
                        logger.info(f"[ClaudeCodeExecutor] SDK session stored: {new_sid}")

                # Stream tool use and planning text as intermediate progress artifacts
                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text = block.text.strip()
                            if text:
                                step_counter += 1
                                await updater.add_artifact(
                                    [Part(root=TextPart(text=text))],
                                    name=f"code_step_{step_counter}"
                                )
                        elif isinstance(block, ToolUseBlock):
                            tool_name = block.name

                            if tool_name == "TodoWrite":
                                todo_counter += 1
                                todos = block.input.get("todos", [])
                                last_todos = todos
                                await updater.add_artifact(
                                    [Part(root=TextPart(text=json.dumps(todos)))],
                                    name=f"code_todos_{todo_counter}"
                                )
                                done = sum(1 for t in todos if t.get("status") == "completed")
                                logger.info(f"[ClaudeCodeExecutor] Todos: {done}/{len(todos)} completed")
                            else:
                                if tool_name in ("Write", "Edit"):
                                    fp = block.input.get("file_path", "")
                                    if fp:
                                        files_changed.add(fp)

                                step_counter += 1
                                step_text = _format_tool_step(step_counter, block)
                                await updater.add_artifact(
                                    [Part(root=TextPart(text=step_text))],
                                    name=f"code_step_{step_counter}"
                                )
                                logger.info(f"[ClaudeCodeExecutor] {step_text}")

                # Capture final result; also persist the session_id for resume
                elif isinstance(message, ResultMessage):
                    final_result = message.result
                    if message.session_id and message.session_id != _sdk_sessions.get(sdk_key):
                        _sdk_sessions[sdk_key] = message.session_id
                        logger.info(f"[ClaudeCodeExecutor] SDK session stored: {message.session_id}")
                    logger.info(f"[ClaudeCodeExecutor] Done: {str(final_result)}")
                    break  # Turn complete

        except (CLIConnectionError, CLIJSONDecodeError) as e:
            logger.exception("[ClaudeCodeExecutor] CLI communication error")
            # Client is broken — discard and let next call recreate with resume=
            await _disconnect_client(sdk_key)
            await updater.add_artifact(
                [Part(root=TextPart(text=f"Error: CLI communication failed. ({e})"))],
                name="error"
            )
            await updater.failed()
            return
        except ProcessError as e:
            logger.exception("[ClaudeCodeExecutor] CLI process error (exit code: %s)", e.exit_code)
            await _disconnect_client(sdk_key)
            await updater.add_artifact(
                [Part(root=TextPart(text=f"Error: {str(e)}"))],
                name="error"
            )
            await updater.failed()
            return
        except Exception as e:
            logger.exception("[ClaudeCodeExecutor] Unexpected execution error")
            await _disconnect_client(sdk_key)
            await updater.add_artifact(
                [Part(root=TextPart(text=f"Error: {str(e)}"))],
                name="error"
            )
            await updater.failed()
            return

        # --- Emit final result (both on success and cancel) ---
        was_cancelled = cancel_event.is_set()
        result_payload = {
            "status": "cancelled" if was_cancelled else "completed",
            "summary": str(final_result) if final_result else ("(interrupted)" if was_cancelled else ""),
            "files_changed": sorted(files_changed),
            "todos": last_todos,
            "steps": step_counter,
        }
        await updater.add_artifact(
            [Part(root=TextPart(text=json.dumps(result_payload, ensure_ascii=False)))],
            name="code_result"
        )

        # --- Always sync to S3 — critical for cancel case (session continuity) ---
        sync_session(user_id, session_id, workspace, _sdk_sessions.get(sdk_key))

        if was_cancelled:
            await updater.cancel()
        else:
            await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Gracefully cancel a running task using interrupt instead of SIGTERM.

        Flow:
        1. Set cancel_event so execute() loop exits cleanly
        2. Send interrupt() to Claude Code subprocess (like pressing ESC)
        3. Report cancel status to A2A framework

        The subprocess stays alive — next query() reuses the same client.
        """
        logger.info(f"[ClaudeCodeExecutor] Cancel requested for task {context.task_id}")

        # 1. Signal the execute() message loop to stop
        cancel_event = _cancel_events.get(context.task_id)
        if cancel_event:
            cancel_event.set()

        # 2. Send graceful interrupt to Claude Code (not SIGTERM)
        sdk_key = _task_to_sdk_key.get(context.task_id)
        if sdk_key:
            client = _sdk_clients.get(sdk_key)
            if client and client._query:
                try:
                    await client.interrupt()
                    logger.info(f"[ClaudeCodeExecutor] Interrupt sent to client {sdk_key}")
                except Exception as e:
                    logger.warning(f"[ClaudeCodeExecutor] interrupt() failed: {e}")

        # 3. Report cancel to A2A framework
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        try:
            await updater.cancel()
        except Exception as e:
            logger.warning(f"[ClaudeCodeExecutor] updater.cancel() failed: {e}")


# ============================================================
# Helpers
# ============================================================

def _extract_text(context: RequestContext) -> str:
    """Extract plain text from the A2A message parts."""
    if not (context.message and hasattr(context.message, "parts")):
        return ""
    text = ""
    for part in context.message.parts:
        if hasattr(part, "root") and hasattr(part.root, "text"):
            text += part.root.text
        elif hasattr(part, "text"):
            text += part.text
    return text.strip()


def _extract_metadata(context: RequestContext) -> dict:
    """Extract metadata dict from MessageSendParams or Message."""
    metadata = context.metadata or {}
    if not metadata and context.message and hasattr(context.message, "metadata"):
        metadata = context.message.metadata or {}
    return metadata


def _strip_workspace_path(val: str) -> str:
    """Strip /[tmp/]workspaces/{user}/{session}/ prefix, returning relative path."""
    return re.sub(
        r'/(?:tmp/)?workspaces/[^/]+/[^/]+(?:/(.+))?$',
        lambda m: m.group(1) or '.',
        val,
    )


def _format_tool_step(step: int, block: ToolUseBlock) -> str:
    """Format a tool_use block into a human-readable progress string."""
    tool_name = block.name
    tool_input = block.input
    status = TOOL_STATUS_MAP.get(tool_name, f"Running {tool_name}")

    context_info = ""
    if isinstance(tool_input, dict):
        # For search tools, prefer showing the search pattern/query over the directory path
        if tool_name == "Grep":
            key_order = ["pattern", "query", "path", "file_path"]
        elif tool_name == "Glob":
            key_order = ["pattern", "path", "file_path"]
        elif tool_name == "WebSearch":
            key_order = ["query", "pattern"]
        elif tool_name == "WebFetch":
            key_order = ["url", "path", "file_path"]
        else:
            key_order = ["file_path", "path", "command", "query", "pattern"]

        for key in key_order:
            if key in tool_input:
                val = str(tool_input[key])
                val = _strip_workspace_path(val)
                val = val[:120]
                context_info = f": {val}"
                break

    return f"{status}{context_info}"


# ============================================================
# App Factory
# ============================================================

AGENT_SKILLS = [
    AgentSkill(
        id="execute_coding_task",
        name="Execute Coding Task",
        description=(
            "Autonomously implement features, fix bugs, refactor code, and run tests "
            "using Claude Agent SDK tools (Read, Write, Edit, Bash, Glob, Grep). "
            "Maintains session context across multiple calls for iterative workflows."
        ),
        inputModes=["text/plain"],
        outputModes=["text/plain"],
        tags=["coding", "development", "automation", "debugging"],
        examples=[
            "Add input validation to src/auth.py and write unit tests",
            "Fix the failing tests in tests/test_api.py",
            "Refactor the database module to use async/await",
            "Implement a REST endpoint for user profile updates",
        ]
    ),
]


def create_app() -> FastAPI:
    """Create FastAPI application with A2A server."""
    runtime_url = os.environ.get("AGENTCORE_RUNTIME_URL", f"http://127.0.0.1:{PORT}/")

    app = FastAPI(
        title="Code Agent A2A Server",
        description="Autonomous coding agent powered by Claude Agent SDK.",
        version="1.0.0"
    )

    agent_card = AgentCard(
        name="Code Agent",
        description=(
            "Autonomous coding agent. "
            "Implements features, fixes bugs, refactors code, and runs tests "
            "with full file system access."
        ),
        url=runtime_url,
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True),
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        skills=AGENT_SKILLS,
    )

    task_store = InMemoryTaskStore()
    request_handler = DefaultRequestHandler(
        agent_executor=ClaudeCodeExecutor(),
        task_store=task_store,
    )

    a2a_starlette_app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )

    @app.get("/ping")
    def ping():
        return {
            "status": "healthy",
            "agent": "Code Agent",
            "version": "1.0.0",
            "skills": ["execute_coding_task"],
        }

    app.mount("/", a2a_starlette_app.build())
    logger.info(f"Code Agent A2A Server configured at {runtime_url}")
    return app


app = create_app()

if __name__ == "__main__":
    logger.info(f"Starting Code Agent on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
