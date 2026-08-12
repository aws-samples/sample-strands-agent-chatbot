"""
Tests for workspace local tools (workspace_list, workspace_read, workspace_write)

Tests cover:
- Path routing (_to_s3_key, _to_logical_path) for all namespaces
- workspace_list: all namespaces, filtered prefix, empty result, S3 error
- workspace_read: text file, binary file (base64), file not found, S3 error
- workspace_write: text write, base64 binary write, S3 error
- userId/sessionId isolation from tool_context
"""
import json
from datetime import datetime
from unittest.mock import patch, MagicMock


def _make_context(user_id="user123", session_id="sess456"):
    ctx = MagicMock()
    ctx.invocation_state = {"user_id": user_id, "session_id": session_id}
    return ctx


# ============================================================
# Path Routing Tests
# ============================================================

class TestPathRouting:
    """Tests for _to_s3_key and _to_logical_path helpers."""

    def test_code_agent_prefix_maps_correctly(self):
        from local_tools.workspace import _to_s3_key
        key = _to_s3_key("u1", "s1", "code-agent/foo.py")
        assert key == "code-agent-workspace/u1/s1/foo.py"

    def test_code_interpreter_prefix_maps_correctly(self):
        from local_tools.workspace import _to_s3_key
        key = _to_s3_key("u1", "s1", "code-interpreter/chart.png")
        assert key == (
            "code-interpreter-workspace/"
            "4bbb6a0cadc45672cce19f083c25006dc9dd503fa17461d2/chart.png"
        )

    def test_documents_prefix_maps_correctly(self):
        from local_tools.workspace import _to_s3_key
        key = _to_s3_key("u1", "s1", "documents/powerpoint/report.pptx")
        assert key == "documents/u1/s1/powerpoint/report.pptx"

    def test_unknown_prefix_falls_back_to_documents(self):
        from local_tools.workspace import _to_s3_key
        key = _to_s3_key("u1", "s1", "random/file.txt")
        assert key == "documents/u1/s1/random/file.txt"

    def test_leading_slash_is_stripped(self):
        from local_tools.workspace import _to_s3_key
        key = _to_s3_key("u1", "s1", "/code-agent/foo.py")
        assert key == "code-agent-workspace/u1/s1/foo.py"

    def test_logical_path_from_code_agent_s3_key(self):
        from local_tools.workspace import _to_logical_path
        logical = _to_logical_path("u1", "s1", "code-agent-workspace/u1/s1/foo.py")
        assert logical == "code-agent/foo.py"

    def test_logical_path_from_code_interpreter_s3_key(self):
        from local_tools.workspace import _to_logical_path
        logical = _to_logical_path(
            "u1",
            "s1",
            "code-interpreter-workspace/"
            "4bbb6a0cadc45672cce19f083c25006dc9dd503fa17461d2/chart.png",
        )
        assert logical == "code-interpreter/chart.png"

    def test_logical_path_from_mounted_input_key(self):
        from local_tools.workspace import _to_logical_path
        logical = _to_logical_path(
            "u1",
            "s1",
            "code-interpreter-workspace/"
            "4bbb6a0cadc45672cce19f083c25006dc9dd503fa17461d2/inputs/data.json",
        )
        assert logical == "uploads/data.json"

    def test_logical_path_from_documents_s3_key(self):
        from local_tools.workspace import _to_logical_path
        logical = _to_logical_path("u1", "s1", "documents/u1/s1/powerpoint/deck.pptx")
        assert logical == "documents/powerpoint/deck.pptx"

    def test_logical_path_unknown_key_returns_key_as_is(self):
        from local_tools.workspace import _to_logical_path
        key = "other-bucket/u1/s1/file.txt"
        logical = _to_logical_path("u1", "s1", key)
        assert logical == key

    def test_user_session_ids_are_isolated(self):
        from local_tools.workspace import _to_s3_key
        key_a = _to_s3_key("alice", "s1", "code-agent/file.py")
        key_b = _to_s3_key("bob", "s1", "code-agent/file.py")
        assert "alice" in key_a
        assert "bob" in key_b
        assert key_a != key_b


# ============================================================
# workspace_list Tests
# ============================================================

class TestWorkspaceList:
    """Tests for workspace_list tool."""

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_lists_all_namespaces_when_path_empty(self, mock_bucket, mock_s3_factory):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3

        paginator = MagicMock()
        paginator.paginate.return_value = [
            {'Contents': [
                {'Key': 'code-agent-workspace/u1/s1/foo.py', 'Size': 100,
                 'LastModified': datetime(2024, 1, 1)},
            ]},
        ]
        mock_s3.get_paginator.return_value = paginator

        from local_tools.workspace import workspace_list
        result = workspace_list(path='', tool_context=_make_context('u1', 's1'))
        data = json.loads(result)

        assert data['count'] >= 1
        assert any(f['path'] == 'code-agent/foo.py' for f in data['files'])

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_filters_by_prefix(self, mock_bucket, mock_s3_factory):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3

        paginator = MagicMock()
        paginator.paginate.return_value = [
            {'Contents': [
                {'Key': (
                    'code-interpreter-workspace/'
                    '4bbb6a0cadc45672cce19f083c25006dc9dd503fa17461d2/chart.png'
                ), 'Size': 5000,
                 'LastModified': datetime(2024, 1, 1)},
            ]},
        ]
        mock_s3.get_paginator.return_value = paginator

        from local_tools.workspace import workspace_list
        result = workspace_list(path='code-interpreter/', tool_context=_make_context('u1', 's1'))
        data = json.loads(result)

        assert data['count'] == 1
        assert data['files'][0]['path'] == 'code-interpreter/chart.png'

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_returns_empty_list_when_no_files(self, mock_bucket, mock_s3_factory):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3
        paginator = MagicMock()
        paginator.paginate.return_value = [{}]
        mock_s3.get_paginator.return_value = paginator

        from local_tools.workspace import workspace_list
        result = workspace_list(path='', tool_context=_make_context())
        data = json.loads(result)

        assert data['count'] == 0
        assert data['files'] == []

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_skips_directory_marker_keys(self, mock_bucket, mock_s3_factory):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {'Contents': [
                {'Key': 'code-agent-workspace/u1/s1/', 'Size': 0,
                 'LastModified': datetime(2024, 1, 1)},
                {'Key': 'code-agent-workspace/u1/s1/real.py', 'Size': 42,
                 'LastModified': datetime(2024, 1, 1)},
            ]},
        ]
        mock_s3.get_paginator.return_value = paginator

        from local_tools.workspace import workspace_list
        # Use a specific prefix so only one namespace is scanned
        result = workspace_list(path='code-agent/', tool_context=_make_context('u1', 's1'))
        data = json.loads(result)

        assert all(not f['path'].endswith('/') for f in data['files'])
        assert data['count'] == 1

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_returns_error_on_s3_failure(self, mock_bucket, mock_s3_factory):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3
        mock_s3.get_paginator.side_effect = Exception("S3 unavailable")

        from local_tools.workspace import workspace_list
        result = workspace_list(path='', tool_context=_make_context())
        data = json.loads(result)

        assert data['status'] == 'error'
        assert 'S3 unavailable' in data['error']


# ============================================================
# workspace_read Tests
# ============================================================

class TestWorkspaceRead:
    """Tests for workspace_read tool."""

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_reads_text_file(self, mock_bucket, mock_s3_factory):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3
        mock_s3.get_object.return_value = {
            'Body': MagicMock(read=MagicMock(return_value=b'print("hello")'))
        }

        from local_tools.workspace import workspace_read
        result = workspace_read(path='code-agent/script.py', tool_context=_make_context())
        data = json.loads(result)

        assert data['status'] == 'ok'
        assert data['encoding'] == 'text'
        assert data['content'] == 'print("hello")'
        assert data['path'] == 'code-agent/script.py'

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_reads_binary_file_as_base64(self, mock_bucket, mock_s3_factory):
        import base64
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3
        binary_data = b'\x89PNG\r\n\x1a\n'
        mock_s3.get_object.return_value = {
            'Body': MagicMock(read=MagicMock(return_value=binary_data))
        }

        from local_tools.workspace import workspace_read
        result = workspace_read(path='code-interpreter/chart.png', tool_context=_make_context())
        data = json.loads(result)

        assert data['status'] == 'ok'
        assert data['encoding'] == 'base64'
        assert base64.b64decode(data['content']) == binary_data

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_returns_not_found_for_missing_file(self, mock_bucket, mock_s3_factory):
        import botocore.exceptions
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3
        error_response = {'Error': {'Code': 'NoSuchKey', 'Message': 'Not found'}}
        mock_s3.get_object.side_effect = botocore.exceptions.ClientError(error_response, 'GetObject')

        from local_tools.workspace import workspace_read
        result = workspace_read(path='code-agent/missing.py', tool_context=_make_context())
        data = json.loads(result)

        assert data['status'] == 'error'
        assert 'not found' in data['error'].lower() or 'missing.py' in data['error']

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_returns_error_on_s3_failure(self, mock_bucket, mock_s3_factory):
        import botocore.exceptions
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3
        error_response = {'Error': {'Code': 'AccessDenied', 'Message': 'Access denied'}}
        mock_s3.get_object.side_effect = botocore.exceptions.ClientError(error_response, 'GetObject')

        from local_tools.workspace import workspace_read
        result = workspace_read(path='code-agent/secret.py', tool_context=_make_context())
        data = json.loads(result)

        assert data['status'] == 'error'

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_uses_correct_s3_key_for_code_agent(self, mock_bucket, mock_s3_factory):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3
        mock_s3.get_object.return_value = {
            'Body': MagicMock(read=MagicMock(return_value=b'data'))
        }

        from local_tools.workspace import workspace_read
        workspace_read(path='code-agent/output.csv', tool_context=_make_context('userA', 'sessB'))

        called_key = mock_s3.get_object.call_args[1]['Key']
        assert called_key == 'code-agent-workspace/userA/sessB/output.csv'

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_size_is_included_in_response(self, mock_bucket, mock_s3_factory):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3
        mock_s3.get_object.return_value = {
            'Body': MagicMock(read=MagicMock(return_value=b'hello'))
        }

        from local_tools.workspace import workspace_read
        result = workspace_read(path='code-agent/note.txt', tool_context=_make_context())
        data = json.loads(result)

        assert data['size'] == 5

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_truncates_large_uploaded_text_and_points_to_full_mounted_file(
        self,
        mock_bucket,
        mock_s3_factory,
    ):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3
        body = MagicMock()
        body.read.return_value = b'x' * 100_001
        mock_s3.get_object.return_value = {
            'Body': body,
            'ContentLength': 250_000,
        }

        from local_tools.workspace import workspace_read
        result = workspace_read(
            path='uploads/records.jsonl',
            tool_context=_make_context(),
        )
        data = json.loads(result)

        body.read.assert_called_once_with(100_001)
        assert data['status'] == 'ok'
        assert data['encoding'] == 'text'
        assert data['size'] == 100_000
        assert data['truncated'] is True
        assert len(data['content']) == 100_000
        assert '/mnt/workspace/inputs/records.jsonl' in data['full_file']
        assert 'records.jsonl in the working directory' in data['full_file']


# ============================================================
# workspace_write Tests
# ============================================================

class TestWorkspaceWrite:
    """Tests for workspace_write tool."""

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_writes_text_file(self, mock_bucket, mock_s3_factory):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3

        from local_tools.workspace import workspace_write
        result = workspace_write(
            path='documents/word/doc.txt',
            content='Hello world',
            encoding='text',
            tool_context=_make_context(),
        )
        data = json.loads(result)

        assert data['status'] == 'ok'
        assert data['path'] == 'documents/word/doc.txt'
        mock_s3.put_object.assert_called_once()
        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs['Body'] == b'Hello world'

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_writes_base64_binary_file(self, mock_bucket, mock_s3_factory):
        import base64
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3
        original = b'\x89PNG data'
        b64_content = base64.b64encode(original).decode('utf-8')

        from local_tools.workspace import workspace_write
        result = workspace_write(
            path='code-interpreter/image.png',
            content=b64_content,
            encoding='base64',
            tool_context=_make_context(),
        )
        data = json.loads(result)

        assert data['status'] == 'ok'
        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs['Body'] == original

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_uses_correct_s3_key(self, mock_bucket, mock_s3_factory):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3

        from local_tools.workspace import workspace_write
        workspace_write(
            path='code-interpreter/result.json',
            content='{}',
            tool_context=_make_context('alice', 'sess1'),
        )

        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs['Key'] == (
            'code-interpreter-workspace/'
            'e0fba2dec7cc6f707ae0530f028bb3de98c5652a1913e090/result.json'
        )

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_returns_error_on_s3_failure(self, mock_bucket, mock_s3_factory):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3
        mock_s3.put_object.side_effect = Exception("Write failed")

        from local_tools.workspace import workspace_write
        result = workspace_write(
            path='code-agent/file.txt',
            content='data',
            tool_context=_make_context(),
        )
        data = json.loads(result)

        assert data['status'] == 'error'
        assert 'Write failed' in data['error']

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_response_includes_size(self, mock_bucket, mock_s3_factory):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3

        from local_tools.workspace import workspace_write
        result = workspace_write(
            path='code-agent/note.txt',
            content='12345',
            tool_context=_make_context(),
        )
        data = json.loads(result)

        assert data['size'] == 5


# ============================================================
# Binary Detection Tests
# ============================================================

class TestBinaryDetection:
    """Tests for _is_binary helper."""

    def test_png_is_binary(self):
        from local_tools.workspace import _is_binary
        assert _is_binary('chart.png') is True

    def test_jpg_is_binary(self):
        from local_tools.workspace import _is_binary
        assert _is_binary('photo.jpg') is True

    def test_pptx_is_binary(self):
        from local_tools.workspace import _is_binary
        assert _is_binary('deck.pptx') is True

    def test_xlsx_is_binary(self):
        from local_tools.workspace import _is_binary
        assert _is_binary('data.xlsx') is True

    def test_txt_is_not_binary(self):
        from local_tools.workspace import _is_binary
        assert _is_binary('notes.txt') is False

    def test_py_is_not_binary(self):
        from local_tools.workspace import _is_binary
        assert _is_binary('script.py') is False

    def test_json_is_not_binary(self):
        from local_tools.workspace import _is_binary
        assert _is_binary('data.json') is False

    def test_case_insensitive(self):
        from local_tools.workspace import _is_binary
        assert _is_binary('IMAGE.PNG') is True
        assert _is_binary('SCRIPT.PY') is False


# ============================================================
# Response Format Tests
# ============================================================

class TestResponseFormat:
    """Tests that all tools return valid JSON with required fields."""

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_list_returns_valid_json(self, mock_bucket, mock_s3_factory):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3
        mock_s3.get_paginator.side_effect = Exception("boom")

        from local_tools.workspace import workspace_list
        result = workspace_list(path='', tool_context=_make_context())
        data = json.loads(result)
        assert isinstance(data, dict)

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_read_returns_valid_json(self, mock_bucket, mock_s3_factory):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3
        mock_s3.get_object.side_effect = Exception("boom")

        from local_tools.workspace import workspace_read
        result = workspace_read(path='code-agent/x.py', tool_context=_make_context())
        data = json.loads(result)
        assert isinstance(data, dict)

    @patch('local_tools.workspace._s3_client')
    @patch('local_tools.workspace.get_workspace_bucket', return_value='my-bucket')
    def test_write_returns_valid_json(self, mock_bucket, mock_s3_factory):
        mock_s3 = MagicMock()
        mock_s3_factory.return_value = mock_s3
        mock_s3.put_object.side_effect = Exception("boom")

        from local_tools.workspace import workspace_write
        result = workspace_write(path='x.txt', content='hi', tool_context=_make_context())
        data = json.loads(result)
        assert isinstance(data, dict)
    def test_uploads_prefix_maps_to_mounted_inputs(self):
        from local_tools.workspace import _to_s3_key
        key = _to_s3_key("u1", "s1", "uploads/data.jsonl")
        assert key == (
            "code-interpreter-workspace/"
            "4bbb6a0cadc45672cce19f083c25006dc9dd503fa17461d2/inputs/data.jsonl"
        )
