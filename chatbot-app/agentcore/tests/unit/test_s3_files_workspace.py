from unittest.mock import MagicMock, patch

import pytest


def _state():
    values = {}
    state = MagicMock()
    state.get.side_effect = values.get
    state.set.side_effect = values.__setitem__
    return state, values


@patch.dict(
    "os.environ",
    {
        "S3_FILES_FILE_SYSTEM_ID": "fs-123",
        "S3_FILES_FILE_SYSTEM_ARN": "arn:aws:s3files:us-west-2:123:file-system/fs-123",
        "S3_FILES_MOUNT_PATH": "/mnt/workspace",
    },
    clear=False,
)
@patch("workspace.s3_files.time.sleep")
@patch("workspace.s3_files.boto3.client")
def test_creates_session_scoped_access_point(mock_client, _sleep):
    client = mock_client.return_value
    client.create_access_point.return_value = {
        "accessPointId": "ap-123",
        "accessPointArn": "arn:aws:s3files:us-west-2:123:access-point/ap-123",
    }
    client.get_access_point.return_value = {
        "status": "available",
        "accessPointArn": "arn:aws:s3files:us-west-2:123:access-point/ap-123",
    }
    state, values = _state()

    from workspace.s3_files import get_or_create_session_access_point

    result = get_or_create_session_access_point(state, "user-1", "session-1")

    assert result["access_point_id"] == "ap-123"
    assert result["mount_path"] == "/mnt/workspace"
    request = client.create_access_point.call_args.kwargs
    assert request["fileSystemId"] == "fs-123"
    assert request["rootDirectory"]["path"] == (
        "/code-interpreter-workspace/user-1/session-1"
    )
    assert "tags" not in request
    assert values["ci_workspace_access_point_id"] == "ap-123"


@patch.dict(
    "os.environ",
    {
        "S3_FILES_FILE_SYSTEM_ID": "fs-123",
        "S3_FILES_FILE_SYSTEM_ARN": "arn:aws:s3files:us-west-2:123:file-system/fs-123",
    },
    clear=False,
)
def test_rejects_identity_that_can_escape_root():
    from workspace.s3_files import get_or_create_session_access_point

    with pytest.raises(ValueError):
        get_or_create_session_access_point(MagicMock(), "user/other", "session-1")


@patch.dict("os.environ", {}, clear=True)
def test_returns_none_when_s3_files_is_disabled():
    from workspace.s3_files import get_or_create_session_access_point

    assert get_or_create_session_access_point(
        MagicMock(),
        "user-1",
        "session-1",
    ) is None
