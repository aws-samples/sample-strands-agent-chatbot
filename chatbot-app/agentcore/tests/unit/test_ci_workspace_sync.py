"""
Tests for CI workspace sync tools (ci_push_to_workspace)
and the shared CI session helper (get_ci_session / _get_ci_from_context).

The original helpers _is_text_file, _ws_path_to_s3_key, _extract_file_list,
and the ci_pull_from_workspace tool were removed in a refactor that simplified
the CI tools to use ci.invoke() directly.  These tests cover the current API.
"""
import json
from unittest.mock import MagicMock, patch


def _make_context(user_id="user1", session_id="sess1"):
    ctx = MagicMock()
    ctx.invocation_state = {"user_id": user_id, "session_id": session_id}
    return ctx


def _text_invoke_response(text: str) -> dict:
    """Build a ci.invoke() response carrying a text content block."""
    return {"stream": [{"result": {"content": [{"text": text}]}}]}


def _binary_invoke_response(data: bytes) -> dict:
    """Build a ci.invoke() response carrying a binary data content block."""
    return {"stream": [{"result": {"content": [{"data": data}]}}]}


def _exec_code_invoke_response(stdout: str) -> dict:
    """Build a ci.invoke('executeCode') response via _parse_stream format."""
    return {
        "stream": [{
            "result": {
                "structuredContent": {"stdout": stdout, "stderr": ""},
                "isError": False,
            }
        }]
    }


def test_mounted_javascript_runs_from_workspace(monkeypatch):
    monkeypatch.setenv("S3_FILES_MOUNT_PATH", "/mnt/workspace")

    from builtin_tools.code_interpreter_tool import (
        _prepare_code_for_mounted_workspace,
    )

    prepared = _prepare_code_for_mounted_workspace(
        "await Deno.writeTextFile('output.txt', 'ok')",
        "javascript",
    )

    assert prepared.startswith('Deno.chdir("/mnt/workspace");\n')
    assert prepared.endswith("await Deno.writeTextFile('output.txt', 'ok')")


def test_mounted_python_code_is_not_rewritten():
    from builtin_tools.code_interpreter_tool import (
        _prepare_code_for_mounted_workspace,
    )

    assert _prepare_code_for_mounted_workspace(
        "open('output.txt', 'w').write('ok')",
        "python",
    ) == "open('output.txt', 'w').write('ok')"


# ============================================================
# ci_push_to_workspace Tests
# ============================================================

class TestCiPushToWorkspace:
    """Tests for ci_push_to_workspace tool (current implementation)."""

    @patch('builtin_tools.code_interpreter_tool._get_ci_from_context')
    @patch('builtin_tools.code_interpreter_tool._save_to_workspace')
    def test_pushes_specific_text_file(self, mock_save, mock_get_ci):
        ci = MagicMock()
        mock_get_ci.return_value = ci
        mock_save.return_value = {'doc_type': 'code-output', 's3_key': 'k', 'bucket': 'b'}
        ci.invoke.return_value = _text_invoke_response("x = 1\n")

        from builtin_tools.code_interpreter_tool import ci_push_to_workspace
        result = ci_push_to_workspace(paths=["script.py"], tool_context=_make_context())
        data = json.loads(result)

        assert data['status'] == 'ok'
        assert data['count'] == 1
        assert "code-output/script.py" in data['files_saved']
        mock_save.assert_called_once()

    @patch('builtin_tools.code_interpreter_tool._get_ci_from_context')
    @patch('builtin_tools.code_interpreter_tool._save_to_workspace')
    def test_pushes_binary_blob_file(self, mock_save, mock_get_ci):
        ci = MagicMock()
        mock_get_ci.return_value = ci
        mock_save.return_value = {'doc_type': 'image', 's3_key': 'k', 'bucket': 'b'}
        png_bytes = b'\x89PNG\r\n\x1a\n'
        ci.invoke.return_value = _binary_invoke_response(png_bytes)

        from builtin_tools.code_interpreter_tool import ci_push_to_workspace
        result = ci_push_to_workspace(paths=["chart.png"], tool_context=_make_context())
        data = json.loads(result)

        assert data['status'] == 'ok'
        assert data['count'] == 1
        mock_save.assert_called_once()
        saved_bytes = mock_save.call_args[0][2]
        assert saved_bytes == png_bytes

    @patch('builtin_tools.code_interpreter_tool._get_ci_from_context')
    @patch('builtin_tools.code_interpreter_tool._save_to_workspace')
    def test_auto_discovers_files_when_no_paths(self, mock_save, mock_get_ci):
        ci = MagicMock()
        mock_get_ci.return_value = ci
        mock_save.return_value = {'doc_type': 'code-output', 's3_key': 'k', 'bucket': 'b'}

        def invoke_side_effect(operation, params):
            if operation == "executeCode":
                return _exec_code_invoke_response('["auto_file.csv"]')
            return _text_invoke_response("a,b\n1,2\n")

        ci.invoke.side_effect = invoke_side_effect

        from builtin_tools.code_interpreter_tool import ci_push_to_workspace
        result = ci_push_to_workspace(paths=None, tool_context=_make_context())
        data = json.loads(result)

        assert data['status'] == 'ok'
        assert data['count'] == 1
        # First call should be the file-listing executeCode
        first_call = ci.invoke.call_args_list[0]
        assert first_call[0][0] == "executeCode"

    @patch('builtin_tools.code_interpreter_tool._get_ci_from_context')
    def test_returns_empty_when_no_files_discovered(self, mock_get_ci):
        ci = MagicMock()
        mock_get_ci.return_value = ci
        ci.invoke.return_value = _exec_code_invoke_response("[]")

        from builtin_tools.code_interpreter_tool import ci_push_to_workspace
        result = ci_push_to_workspace(paths=None, tool_context=_make_context())
        data = json.loads(result)

        assert data['status'] == 'ok'
        assert data['count'] == 0
        assert data['files_saved'] == []

    @patch('builtin_tools.code_interpreter_tool._get_ci_from_context')
    @patch('builtin_tools.code_interpreter_tool._save_to_workspace')
    def test_skips_failed_file_and_continues(self, mock_save, mock_get_ci):
        ci = MagicMock()
        mock_get_ci.return_value = ci
        mock_save.return_value = {'doc_type': 'code-output', 's3_key': 'k', 'bucket': 'b'}

        def invoke_side_effect(operation, params):
            if operation == "readFiles" and "fail.py" in params.get("paths", []):
                raise Exception("read error")
            return _text_invoke_response("ok")

        ci.invoke.side_effect = invoke_side_effect

        from builtin_tools.code_interpreter_tool import ci_push_to_workspace
        result = ci_push_to_workspace(
            paths=["ok.py", "fail.py"],
            tool_context=_make_context(),
        )
        data = json.loads(result)

        assert data['status'] == 'ok'
        assert data['count'] == 1
        assert any("ok.py" in p for p in data['files_saved'])

    @patch('builtin_tools.code_interpreter_tool._get_ci_from_context')
    def test_returns_error_when_ci_not_available(self, mock_get_ci):
        mock_get_ci.return_value = None

        from builtin_tools.code_interpreter_tool import ci_push_to_workspace
        result = ci_push_to_workspace(paths=["file.py"], tool_context=_make_context())
        data = json.loads(result)

        assert data['status'] == 'error'
        assert 'not available' in data['error'].lower()


# ============================================================
# Response Format Tests
# ============================================================

class TestCiSyncResponseFormat:
    """Tests that sync tools always return valid JSON."""

    @patch('builtin_tools.code_interpreter_tool._get_ci_from_context')
    def test_push_error_returns_valid_json(self, mock_get_ci):
        ci = MagicMock()
        mock_get_ci.return_value = ci
        # Make auto-discovery (executeCode) raise so the outer except fires
        ci.invoke.side_effect = Exception("CI exploded")

        from builtin_tools.code_interpreter_tool import ci_push_to_workspace
        result = ci_push_to_workspace(paths=None, tool_context=_make_context())
        data = json.loads(result)
        assert isinstance(data, dict)
        assert data['status'] == 'error'

    @patch('builtin_tools.code_interpreter_tool._get_ci_from_context')
    def test_push_unavailable_returns_valid_json(self, mock_get_ci):
        mock_get_ci.return_value = None

        from builtin_tools.code_interpreter_tool import ci_push_to_workspace
        result = ci_push_to_workspace(
            paths=["code-output/x.csv"],
            tool_context=_make_context(),
        )
        data = json.loads(result)
        assert isinstance(data, dict)
        assert 'status' in data
