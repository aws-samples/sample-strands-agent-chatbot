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
        "/code-interpreter-workspace/"
        "c75baf0822512599e9fb5404e22693cffa5c19b706f1f6c2"
    )
    assert len(request["rootDirectory"]["path"]) <= 100
    assert request["posixUser"] == {"uid": 0, "gid": 0}
    assert request["rootDirectory"]["creationPermissions"] == {
        "ownerUid": 0,
        "ownerGid": 0,
        "permissions": "0750",
    }
    assert "tags" not in request
    assert values["ci_workspace_access_point_v2_id"] == "ap-123"


@patch.dict(
    "os.environ",
    {
        "S3_FILES_FILE_SYSTEM_ID": "fs-123",
        "S3_FILES_FILE_SYSTEM_ARN": "arn:aws:s3files:us-west-2:123:file-system/fs-123",
    },
    clear=False,
)
@patch("workspace.s3_files.boto3.client")
def test_reuses_only_root_identity_access_point(mock_client):
    client = mock_client.return_value
    root_path = (
        "/code-interpreter-workspace/"
        "c75baf0822512599e9fb5404e22693cffa5c19b706f1f6c2"
    )
    client.get_access_point.return_value = {
        "status": "available",
        "accessPointArn": "arn:aws:s3files:us-west-2:123:access-point/ap-123",
        "posixUser": {"uid": 0, "gid": 0},
        "rootDirectory": {"path": root_path},
    }
    state, _ = _state()
    state.get.side_effect = {
        "ci_workspace_access_point_v2_id": "ap-123",
        "ci_workspace_access_point_v2_arn": (
            "arn:aws:s3files:us-west-2:123:access-point/ap-123"
        ),
    }.get

    from workspace.s3_files import get_or_create_session_access_point

    result = get_or_create_session_access_point(state, "user-1", "session-1")

    assert result["access_point_id"] == "ap-123"
    client.create_access_point.assert_not_called()


@patch.dict(
    "os.environ",
    {
        "S3_FILES_FILE_SYSTEM_ID": "fs-123",
        "S3_FILES_FILE_SYSTEM_ARN": "arn:aws:s3files:us-west-2:123:file-system/fs-123",
    },
    clear=False,
)
@patch("workspace.s3_files.time.sleep")
@patch("workspace.s3_files.boto3.client")
def test_replaces_incompatible_access_point(mock_client, _sleep):
    client = mock_client.return_value
    old_arn = "arn:aws:s3files:us-west-2:123:access-point/ap-old"
    new_arn = "arn:aws:s3files:us-west-2:123:access-point/ap-new"
    client.get_access_point.side_effect = [
        {
            "status": "available",
            "accessPointArn": old_arn,
            "posixUser": {"uid": 1000, "gid": 1000},
            "rootDirectory": {"path": "/old"},
        },
        {
            "status": "available",
            "accessPointArn": new_arn,
        },
    ]
    client.create_access_point.return_value = {
        "accessPointId": "ap-new",
        "accessPointArn": new_arn,
    }
    state, _ = _state()
    state.get.side_effect = {
        "ci_workspace_access_point_v2_id": "ap-old",
        "ci_workspace_access_point_v2_arn": old_arn,
    }.get

    from workspace.s3_files import get_or_create_session_access_point

    result = get_or_create_session_access_point(state, "user-1", "session-1")

    assert result["access_point_id"] == "ap-new"
    client.delete_access_point.assert_called_once_with(accessPointId="ap-old")


def test_workspace_path_is_bounded_for_long_runtime_session_ids():
    from workspace.paths import code_interpreter_prefix

    root_path = "/" + code_interpreter_prefix("u" * 64, "s" * 100).rstrip("/")

    assert len(root_path) == 76


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
def test_raises_when_s3_files_is_not_configured():
    from workspace.s3_files import get_or_create_session_access_point

    with pytest.raises(RuntimeError, match="S3 Files workspace is not configured"):
        get_or_create_session_access_point(
            MagicMock(),
            "user-1",
            "session-1",
        )
