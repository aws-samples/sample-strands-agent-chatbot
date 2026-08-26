"""Tests for the required mounted Code Interpreter workspace."""

import json
from unittest.mock import MagicMock, patch

import pytest


def _make_context(user_id="user1", session_id="sess1", state_values=None):
    values = dict(state_values or {})
    state = MagicMock()
    state.get.side_effect = values.get
    state.set.side_effect = values.__setitem__

    ctx = MagicMock()
    ctx.invocation_state = {"user_id": user_id, "session_id": session_id}
    ctx.tool_use = {"toolUseId": "tool-use-1"}
    ctx.agent.state = state
    return ctx, values


def _exec_response(stdout: str = "") -> dict:
    return {
        "stream": [{
            "result": {
                "structuredContent": {"stdout": stdout, "stderr": ""},
                "isError": False,
            }
        }]
    }


@pytest.fixture(autouse=True)
def _clear_ci_clients():
    from builtin_tools import code_interpreter_tool

    code_interpreter_tool._ci_clients.clear()
    yield
    code_interpreter_tool._ci_clients.clear()


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


def test_mounted_path_resolves_relative_paths_and_rejects_escape(monkeypatch):
    monkeypatch.setenv("S3_FILES_MOUNT_PATH", "/mnt/workspace")

    from builtin_tools.code_interpreter_tool import _mounted_path

    assert _mounted_path("reports/result.md") == "/mnt/workspace/reports/result.md"
    assert _mounted_path("/mnt/workspace/result.md") == "/mnt/workspace/result.md"
    with pytest.raises(ValueError, match="inside the session workspace"):
        _mounted_path("../other-session/result.md")
    with pytest.raises(ValueError, match="inside the session workspace"):
        _mounted_path("/tmp/result.md")


@patch("workspace.s3_files.get_or_create_session_access_point")
@patch("bedrock_agentcore.tools.code_interpreter_client.CodeInterpreter")
@patch("builtin_tools.code_interpreter_tool._get_code_interpreter_id")
def test_starts_session_with_required_filesystem_configuration(
    mock_get_identifier,
    mock_code_interpreter,
    mock_access_point,
):
    mock_get_identifier.return_value = "ci-123"
    mock_access_point.return_value = {
        "file_system_arn": "arn:aws:s3files:us-west-2:123:file-system/fs-123",
        "access_point_arn": "arn:aws:s3files:us-west-2:123:access-point/ap-123",
        "mount_path": "/mnt/workspace",
    }
    ci = mock_code_interpreter.return_value
    ci.data_plane_client.start_code_interpreter_session.return_value = {
        "codeInterpreterIdentifier": "ci-123",
        "sessionId": "session-123",
    }
    ci.invoke.return_value = _exec_response()
    ctx, values = _make_context()

    from builtin_tools.code_interpreter_tool import get_ci_session

    assert get_ci_session(ctx) is ci
    request = ci.data_plane_client.start_code_interpreter_session.call_args.kwargs
    assert request["filesystemConfigurations"] == [{
        "s3FilesConfiguration": {
            "fileSystemArn": "arn:aws:s3files:us-west-2:123:file-system/fs-123",
            "accessPointArn": "arn:aws:s3files:us-west-2:123:access-point/ap-123",
            "mountPath": "/mnt/workspace",
        },
    }]
    ci.start.assert_not_called()
    assert values["ci_mounted_workspace"] == "root-v2"
    assert values["ci_session_id"] == "session-123"


@patch("workspace.s3_files.get_or_create_session_access_point")
@patch("bedrock_agentcore.tools.code_interpreter_client.CodeInterpreter")
@patch("builtin_tools.code_interpreter_tool._get_code_interpreter_id")
def test_mount_start_failure_does_not_start_unmounted_session(
    mock_get_identifier,
    mock_code_interpreter,
    mock_access_point,
):
    mock_get_identifier.return_value = "ci-123"
    mock_access_point.return_value = {
        "file_system_arn": "fs-arn",
        "access_point_arn": "ap-arn",
        "mount_path": "/mnt/workspace",
    }
    ci = mock_code_interpreter.return_value
    ci.data_plane_client.start_code_interpreter_session.side_effect = RuntimeError(
        "mount rejected"
    )
    ctx, _ = _make_context()

    from builtin_tools.code_interpreter_tool import get_ci_session

    with pytest.raises(RuntimeError, match="S3 Files workspace"):
        get_ci_session(ctx)
    ci.start.assert_not_called()


@patch("workspace.s3_files.get_or_create_session_access_point")
@patch("bedrock_agentcore.tools.code_interpreter_client.CodeInterpreter")
def test_missing_workspace_configuration_fails_fast(
    mock_code_interpreter,
    mock_access_point,
):
    mock_access_point.side_effect = RuntimeError("S3 Files workspace is not configured")
    ctx, _ = _make_context(state_values={"ci_identifier": "ci-123"})

    from builtin_tools.code_interpreter_tool import get_ci_session

    with pytest.raises(RuntimeError, match="not configured"):
        get_ci_session(ctx)
    mock_code_interpreter.return_value.start.assert_not_called()


@patch("workspace.s3_files.get_or_create_session_access_point")
@patch("bedrock_agentcore.tools.code_interpreter_client.CodeInterpreter")
def test_non_mounted_stored_session_is_not_reattached(
    mock_code_interpreter,
    mock_access_point,
):
    mock_access_point.return_value = {
        "file_system_arn": "fs-arn",
        "access_point_arn": "ap-arn",
        "mount_path": "/mnt/workspace",
    }
    ci = mock_code_interpreter.return_value
    ci.data_plane_client.start_code_interpreter_session.return_value = {
        "codeInterpreterIdentifier": "ci-123",
        "sessionId": "new-mounted-session",
    }
    ci.invoke.return_value = _exec_response()
    ctx, values = _make_context(state_values={
        "ci_identifier": "ci-123",
        "ci_session_id": "old-unmounted-session",
        "ci_mounted_workspace": False,
    })

    from builtin_tools.code_interpreter_tool import get_ci_session

    get_ci_session(ctx)

    ci.get_session.assert_not_called()
    ci.data_plane_client.start_code_interpreter_session.assert_called_once()
    assert values["ci_session_id"] == "new-mounted-session"
    assert values["ci_mounted_workspace"] == "root-v2"


@patch("workspace.s3_files.get_or_create_session_access_point")
@patch("bedrock_agentcore.tools.code_interpreter_client.CodeInterpreter")
def test_legacy_mounted_session_is_replaced(
    mock_code_interpreter,
    mock_access_point,
):
    mock_access_point.return_value = {
        "file_system_arn": "fs-arn",
        "access_point_arn": "ap-arn",
        "mount_path": "/mnt/workspace",
    }
    ci = mock_code_interpreter.return_value
    ci.data_plane_client.start_code_interpreter_session.return_value = {
        "codeInterpreterIdentifier": "ci-123",
        "sessionId": "new-mounted-session",
    }
    ci.invoke.return_value = _exec_response()
    ctx, values = _make_context(state_values={
        "ci_identifier": "ci-123",
        "ci_session_id": "legacy-mounted-session",
        "ci_mounted_workspace": True,
    })

    from builtin_tools.code_interpreter_tool import get_ci_session

    get_ci_session(ctx)

    ci.get_session.assert_not_called()
    assert values["ci_session_id"] == "new-mounted-session"
    assert values["ci_mounted_workspace"] == "root-v2"


def test_workspace_initialization_includes_write_probe():
    ci = MagicMock()
    ci.invoke.return_value = _exec_response()

    from builtin_tools.code_interpreter_tool import _prepare_mounted_workspace

    _prepare_mounted_workspace(ci, verify_write=True)

    code = ci.invoke.call_args.args[1]["code"]
    assert "NamedTemporaryFile" in code
    assert "dir=_mount" in code


@patch("builtin_tools.code_interpreter_tool._get_ci_from_context")
def test_file_operations_writes_only_to_mounted_workspace(mock_get_ci):
    ci = MagicMock()
    ci.invoke.return_value = _exec_response("Written: /mnt/workspace/result.md\n")
    mock_get_ci.return_value = ci
    ctx, _ = _make_context()

    from builtin_tools.code_interpreter_tool import file_operations

    result = file_operations(
        operation="write",
        content=[{"path": "result.md", "text": "# Result"}],
        tool_context=ctx,
    )

    assert "Written: /mnt/workspace/result.md" in result
    code = ci.invoke.call_args.args[1]["code"]
    assert "os.makedirs(os.path.dirname('/mnt/workspace/result.md')" in code
    assert "open('/mnt/workspace/result.md', 'w', encoding='utf-8')" in code


@patch("builtin_tools.code_interpreter_tool._get_ci_from_context")
def test_file_operations_reads_mounted_files_via_python(mock_get_ci):
    ci = MagicMock()
    ci.invoke.return_value = _exec_response(json.dumps([{
        "path": "/mnt/workspace/smoke/result.txt",
        "text": "MOUNTED_WORKSPACE_SMOKE_OK",
        "truncated": False,
    }]))
    mock_get_ci.return_value = ci
    ctx, _ = _make_context()

    from builtin_tools.code_interpreter_tool import file_operations

    result = file_operations(
        operation="read",
        paths=["smoke/result.txt"],
        tool_context=ctx,
    )

    assert result == "MOUNTED_WORKSPACE_SMOKE_OK"
    method, request = ci.invoke.call_args.args
    assert method == "executeCode"
    assert "/mnt/workspace/smoke/result.txt" in request["code"]


@patch("builtin_tools.code_interpreter_tool._get_ci_from_context")
def test_session_initialization_errors_are_returned_by_tools(mock_get_ci):
    mock_get_ci.side_effect = RuntimeError("S3 Files workspace is not configured")
    ctx, _ = _make_context()

    from builtin_tools.code_interpreter_tool import execute_code

    result = json.loads(execute_code("print('hello')", tool_context=ctx))

    assert result["status"] == "error"
    assert "S3 Files workspace is not configured" in result["error"]


@patch("builtin_tools.code_interpreter_tool.SessionFilePublisher.from_environment")
@patch("builtin_tools.code_interpreter_tool._get_ci_from_context")
def test_execute_code_publishes_structured_session_file(
    mock_get_ci,
    mock_publisher_factory,
):
    from session_files.models import SessionFileRef

    ci = MagicMock()
    ci.invoke.return_value = _exec_response("/mnt/workspace/report.pdf\n")
    mock_get_ci.return_value = ci
    publisher = mock_publisher_factory.return_value
    publisher.publish_code_interpreter_file.return_value = SessionFileRef(
        file_id="file-123",
        filename="report.pdf",
        media_type="application/pdf",
        artifact_type="application",
        role="OUTPUT",
        state="READY",
        revision=1,
        size_bytes=2048,
        checksum_sha256="checksum",
    )
    ctx, _ = _make_context()

    from builtin_tools.code_interpreter_tool import execute_code

    result = execute_code(
        "open('report.pdf', 'wb').write(b'pdf')",
        output_filename="report.pdf",
        tool_context=ctx,
    )

    payload = json.loads(result["content"][0]["text"])
    assert "Published report.pdf (2.0 KB)" in payload["text"]
    assert payload["metadata"]["files"][0]["fileId"] == "file-123"
    publisher.publish_code_interpreter_file.assert_called_once_with(
        code_interpreter=ci,
        source_path="/mnt/workspace/report.pdf",
        user_id="user1",
        session_id="sess1",
        filename="report.pdf",
        media_type="application/pdf",
        artifact_type="application",
        producer_tool="execute_code",
        producer_id="tool-use-1",
        idempotency_key="tool-use-1:0",
    )
    assert [call.args[0] for call in ci.invoke.call_args_list] == ["executeCode"]
