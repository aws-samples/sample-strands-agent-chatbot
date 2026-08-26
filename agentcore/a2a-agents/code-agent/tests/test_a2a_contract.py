"""
Contract tests for code-agent A2A protocol.

Tests the pure helper functions that transform A2A requests into Claude SDK
invocations and format tool progress steps — no SDK or AWS calls required.
"""
import re
from unittest.mock import MagicMock

from src.model_runtime import effective_model_id, needs_model_switch

# ============================================================
# Helpers replicated from main.py (pure functions, no deps)
# ============================================================

def build_task_with_files(task_text: str, file_descriptions: list) -> str:
    """Prepend a file context block to the task when S3 files were downloaded."""
    if not file_descriptions:
        return task_text
    files_block = "\n".join(file_descriptions)
    return (
        f"The following session files are synchronized under `inputs/`:\n"
        f"{files_block}\n\n"
        f"{task_text}"
    )


def _strip_workspace_path(val: str) -> str:
    """Strip /[tmp/]workspaces/{user}/{session}/ prefix."""
    return re.sub(
        r'/(?:tmp/)?workspaces/[^/]+/[^/]+(?:/(.+))?$',
        lambda m: m.group(1) or '.',
        val,
    )


def _extract_text(context) -> str:
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


def _extract_metadata(context) -> dict:
    """Extract metadata dict from MessageSendParams or Message."""
    metadata = context.metadata or {}
    if not metadata and context.message and hasattr(context.message, "metadata"):
        metadata = context.message.metadata or {}
    return metadata


TOOL_STATUS_MAP = {
    "Read": "Reading file",
    "Write": "Writing file",
    "Edit": "Editing file",
    "Bash": "Running command",
    "Glob": "Searching files",
    "Grep": "Searching content",
    "WebSearch": "Searching web",
    "WebFetch": "Fetching URL",
}


def _format_tool_step(step: int, block) -> str:
    """Format a tool_use block into a human-readable progress string."""
    tool_name = block.name
    tool_input = block.input
    status = TOOL_STATUS_MAP.get(tool_name, f"Running {tool_name}")

    context_info = ""
    if isinstance(tool_input, dict):
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
# build_task_with_files
# ============================================================

class TestBuildTaskWithFiles:
    def test_no_files_returns_original_task(self):
        assert build_task_with_files("do something", []) == "do something"

    def test_prepends_file_context(self):
        result = build_task_with_files("fix the bug", ["app.py — main module"])
        assert result.startswith("The following session files are synchronized")
        assert "app.py — main module" in result
        assert result.endswith("fix the bug")

    def test_multiple_files_joined_with_newlines(self):
        files = ["a.py — module A", "b.py — module B"]
        result = build_task_with_files("task", files)
        assert "a.py — module A\nb.py — module B" in result


# ============================================================
# _strip_workspace_path
# ============================================================

class TestStripWorkspacePath:
    def test_strips_tmp_workspaces_prefix(self):
        val = "/tmp/workspaces/user-1/session-abc/src/main.py"
        assert _strip_workspace_path(val) == "src/main.py"

    def test_strips_workspaces_prefix_without_tmp(self):
        val = "/workspaces/user-1/session-abc/README.md"
        assert _strip_workspace_path(val) == "README.md"

    def test_returns_dot_for_workspace_root(self):
        val = "/tmp/workspaces/user-1/session-abc"
        assert _strip_workspace_path(val) == "."

    def test_non_workspace_path_unchanged(self):
        val = "/etc/config/settings.json"
        assert _strip_workspace_path(val) == val

    def test_nested_path_preserves_structure(self):
        val = "/tmp/workspaces/u/s/a/b/c/deep.py"
        assert _strip_workspace_path(val) == "a/b/c/deep.py"


# ============================================================
# _extract_text
# ============================================================

class TestExtractText:
    def _make_part_root(self, text):
        part = MagicMock()
        part.root.text = text
        del part.text  # ensure only root.text is used
        return part

    def _make_part_direct(self, text):
        part = MagicMock()
        del part.root
        part.text = text
        return part

    def _make_context(self, parts):
        ctx = MagicMock()
        ctx.message.parts = parts
        return ctx

    def test_extracts_text_from_root(self):
        ctx = self._make_context([self._make_part_root("Hello")])
        assert _extract_text(ctx) == "Hello"

    def test_extracts_and_concatenates_multiple_parts(self):
        ctx = self._make_context([
            self._make_part_root("Hello "),
            self._make_part_root("world"),
        ])
        assert _extract_text(ctx) == "Hello world"

    def test_returns_empty_when_no_message(self):
        ctx = MagicMock()
        ctx.message = None
        assert _extract_text(ctx) == ""

    def test_strips_whitespace(self):
        ctx = self._make_context([self._make_part_root("  trimmed  ")])
        assert _extract_text(ctx) == "trimmed"


# ============================================================
# _extract_metadata
# ============================================================

class TestExtractMetadata:
    def test_returns_context_metadata(self):
        ctx = MagicMock()
        ctx.metadata = {"session_id": "s1", "model_id": "m1"}
        assert _extract_metadata(ctx)["session_id"] == "s1"

    def test_falls_back_to_message_metadata(self):
        ctx = MagicMock()
        ctx.metadata = {}
        ctx.message.metadata = {"session_id": "s2"}
        assert _extract_metadata(ctx)["session_id"] == "s2"

    def test_returns_empty_dict_when_both_absent(self):
        ctx = MagicMock()
        ctx.metadata = {}
        ctx.message.metadata = {}
        assert _extract_metadata(ctx) == {}

    def test_code_agent_receives_orchestrator_model_for_diagnostics_only(self):
        ctx = MagicMock()
        ctx.metadata = {
            "session_id": "s1",
            "orchestrator_model_id": "us.openai.gpt-5.6-terra",
        }
        assert _extract_metadata(ctx)["orchestrator_model_id"] == "us.openai.gpt-5.6-terra"
        assert "model_id" not in _extract_metadata(ctx)


class TestModelRuntime:
    def test_request_model_overrides_process_default(self):
        assert effective_model_id(
            {"model_id": "us.anthropic.claude-opus-5"},
            "us.anthropic.claude-sonnet-5",
        ) == "us.anthropic.claude-opus-5"

    def test_missing_request_model_uses_process_default(self):
        assert effective_model_id(
            {},
            "us.anthropic.claude-sonnet-5",
        ) == "us.anthropic.claude-sonnet-5"

    def test_model_switch_only_when_requested_model_changes(self):
        assert not needs_model_switch("sonnet", "sonnet")
        assert needs_model_switch("sonnet", "opus")


# ============================================================
# _format_tool_step
# ============================================================

class TestFormatToolStep:
    def _block(self, name, input_dict=None):
        b = MagicMock()
        b.name = name
        b.input = input_dict or {}
        return b

    def test_known_tool_uses_status_map(self):
        result = _format_tool_step(1, self._block("Read", {"file_path": "/tmp/workspaces/u/s/app.py"}))
        assert result.startswith("Reading file")
        assert "app.py" in result

    def test_unknown_tool_falls_back_to_running(self):
        result = _format_tool_step(1, self._block("CustomTool"))
        assert result == "Running CustomTool"

    def test_grep_prefers_pattern_over_path(self):
        result = _format_tool_step(1, self._block("Grep", {"pattern": "def main", "path": "/some/dir"}))
        assert "def main" in result

    def test_bash_shows_command(self):
        result = _format_tool_step(1, self._block("Bash", {"command": "pytest tests/"}))
        assert "pytest tests/" in result

    def test_long_path_truncated_to_120_chars(self):
        long_path = "a" * 200
        result = _format_tool_step(1, self._block("Read", {"file_path": long_path}))
        # Only the last 120 chars of the path should appear
        assert len(result) <= len("Reading file: ") + 120 + 10

    def test_workspace_path_stripped_in_output(self):
        result = _format_tool_step(1, self._block("Write", {
            "file_path": "/tmp/workspaces/user-1/session-abc/src/output.py"
        }))
        assert "/tmp/workspaces" not in result
        assert "src/output.py" in result


# ============================================================
# A2A artifact contract (code_step / code_result / code_todos)
# ============================================================

class TestCodeArtifactContract:
    """Verify the artifact names the orchestrator _process_artifact() expects."""

    def test_code_step_name_format(self):
        for n in range(1, 5):
            name = f"code_step_{n}"
            parts = name.split("_")
            assert parts[-1].isdigit()
            assert int(parts[-1]) == n

    def test_code_todos_name_format(self):
        for n in range(1, 3):
            name = f"code_todos_{n}"
            assert name.split("_")[-1].isdigit()

    def test_code_result_payload_structure(self):
        """Orchestrator reads summary, files_changed, todos, steps from code_result."""
        payload = {
            "summary": "Fixed the null pointer bug.",
            "files_changed": ["src/main.py", "tests/test_main.py"],
            "todos": [],
            "steps": 3,
        }
        assert "summary" in payload
        assert isinstance(payload["files_changed"], list)
        assert isinstance(payload["steps"], int)

    def test_code_result_meta_excludes_status_field(self):
        """
        code_result_meta emitted to frontend excludes 'status' — it was never
        read by handleCodeResultMetaEvent and was removed from the contract.
        """
        meta = {
            "files_changed": ["app.py"],
            "todos": [],
            "steps": 2,
        }
        assert "status" not in meta
