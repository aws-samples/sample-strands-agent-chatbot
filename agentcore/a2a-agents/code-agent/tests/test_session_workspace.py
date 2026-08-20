import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.session_workspace import (
    code_interpreter_input_prefix,
    code_interpreter_workspace_id,
    missing_required_inputs,
    normalize_required_input_paths,
    restore_s3_prefix,
    sync_session_inputs,
)


def test_syncs_canonical_uploads_under_inputs(tmp_path: Path):
    input_prefix = code_interpreter_input_prefix("user-1", "session-1")
    s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{
        "Contents": [{
            "Key": (
                f"{input_prefix}/packets-pass-a.jsonl"
            ),
        }],
    }]
    s3.get_paginator.return_value = paginator

    def download_file(bucket, key, destination):
        assert bucket == "workspace-bucket"
        Path(destination).write_text('{"annotation_id":"ca-1"}\n')

    s3.download_file.side_effect = download_file

    descriptions = sync_session_inputs(
        "workspace-bucket",
        "user-1",
        "session-1",
        tmp_path,
        s3,
    )

    assert (tmp_path / "inputs" / "packets-pass-a.jsonl").is_file()
    assert descriptions == ["- `inputs/packets-pass-a.jsonl`"]
    paginator.paginate.assert_called_once_with(
        Bucket="workspace-bucket",
        Prefix=f"{input_prefix}/",
    )


def test_canonical_workspace_id_matches_orchestrator_contract():
    assert code_interpreter_workspace_id("user-1", "session-1") == (
        "c75baf0822512599e9fb5404e22693cffa5c19b706f1f6c2"
    )


def test_workspace_restore_does_not_restore_mirrored_inputs(tmp_path: Path):
    s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{
        "Contents": [
            {"Key": "code-agent-workspace/user-1/session-1/CLAUDE.md"},
            {
                "Key": (
                    "code-agent-workspace/user-1/session-1/"
                    "inputs/stale.jsonl"
                ),
            },
        ],
    }]
    s3.get_paginator.return_value = paginator
    s3.download_file.side_effect = lambda bucket, key, destination: (
        Path(destination).write_text(key)
    )

    restored = restore_s3_prefix(
        tmp_path,
        "workspace-bucket",
        "code-agent-workspace/user-1/session-1",
        s3,
        exclude_top_level_inputs=True,
    )

    assert restored == 1
    assert (tmp_path / "CLAUDE.md").is_file()
    assert not (tmp_path / "inputs" / "stale.jsonl").exists()


def test_sync_failure_preserves_existing_inputs(tmp_path: Path):
    input_prefix = code_interpreter_input_prefix("user-1", "session-1")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "existing.jsonl").write_text('{"existing":true}\n')

    s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{
        "Contents": [{
            "Key": (
                f"{input_prefix}/replacement.jsonl"
            ),
        }],
    }]
    s3.get_paginator.return_value = paginator
    s3.download_file.side_effect = RuntimeError("temporary S3 failure")

    with pytest.raises(RuntimeError, match="temporary S3 failure"):
        sync_session_inputs(
            "workspace-bucket",
            "user-1",
            "session-1",
            tmp_path,
            s3,
        )

    assert (inputs / "existing.jsonl").read_text() == '{"existing":true}\n'
    assert not (inputs / "replacement.jsonl").exists()


def test_sync_listing_failure_preserves_existing_inputs(tmp_path: Path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "existing.jsonl").write_text('{"existing":true}\n')

    s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.side_effect = RuntimeError("temporary listing failure")
    s3.get_paginator.return_value = paginator

    with pytest.raises(RuntimeError, match="temporary listing failure"):
        sync_session_inputs(
            "workspace-bucket",
            "user-1",
            "session-1",
            tmp_path,
            s3,
        )

    assert (inputs / "existing.jsonl").read_text() == '{"existing":true}\n'


def test_sync_rejects_object_path_traversal_and_preserves_inputs(tmp_path: Path):
    input_prefix = code_interpreter_input_prefix("user-1", "session-1")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "existing.txt").write_text("keep")

    s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{
        "Contents": [{
            "Key": (
                f"{input_prefix}/../../outside.txt"
            ),
        }],
    }]
    s3.get_paginator.return_value = paginator

    with pytest.raises(ValueError, match="Unsafe workspace object path"):
        sync_session_inputs(
            "workspace-bucket",
            "user-1",
            "session-1",
            tmp_path,
            s3,
        )

    assert (inputs / "existing.txt").read_text() == "keep"
    assert not (tmp_path / "outside.txt").exists()


def test_sync_replaces_inputs_symlink_without_touching_target(tmp_path: Path):
    input_prefix = code_interpreter_input_prefix("user-1", "session-1")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "protected.txt").write_text("protected")
    (tmp_path / "inputs").symlink_to(outside, target_is_directory=True)

    s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{
        "Contents": [{
            "Key": (
                f"{input_prefix}/current.json"
            ),
        }],
    }]
    s3.get_paginator.return_value = paginator
    s3.download_file.side_effect = lambda bucket, key, destination: (
        Path(destination).write_text("{}")
    )

    sync_session_inputs(
        "workspace-bucket",
        "user-1",
        "session-1",
        tmp_path,
        s3,
    )

    assert not (tmp_path / "inputs").is_symlink()
    assert (tmp_path / "inputs" / "current.json").read_text() == "{}"
    assert (outside / "protected.txt").read_text() == "protected"


def test_restore_rejects_nested_symlink_before_creating_directories(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "nested").symlink_to(outside, target_is_directory=True)

    s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{
        "Contents": [{
            "Key": "workspace-prefix/nested/new/file.json",
        }],
    }]
    s3.get_paginator.return_value = paginator

    with pytest.raises(ValueError, match="escapes destination"):
        restore_s3_prefix(
            workspace,
            "workspace-bucket",
            "workspace-prefix",
            s3,
            strict=True,
        )

    assert not (outside / "new").exists()
    s3.download_file.assert_not_called()


def test_sync_replaces_regular_file_without_leaving_backup(tmp_path: Path):
    input_prefix = code_interpreter_input_prefix("user-1", "session-1")
    (tmp_path / "inputs").write_text("stale")

    s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{
        "Contents": [{
            "Key": (
                f"{input_prefix}/current.json"
            ),
        }],
    }]
    s3.get_paginator.return_value = paginator
    s3.download_file.side_effect = lambda bucket, key, destination: (
        Path(destination).write_text("{}")
    )

    sync_session_inputs(
        "workspace-bucket",
        "user-1",
        "session-1",
        tmp_path,
        s3,
    )

    assert (tmp_path / "inputs" / "current.json").read_text() == "{}"
    assert not list(tmp_path.glob(".inputs-backup-*"))


def test_normalizes_required_workspace_paths():
    assert normalize_required_input_paths([
        "inputs/deck.pptx",
        "uploads/data.json",
        "/mnt/workspace/inputs/deck.pptx",
    ]) == [
        "inputs/deck.pptx",
        "inputs/data.json",
    ]


@pytest.mark.parametrize(
    "paths",
    [
        "inputs/deck.pptx",
        [""],
        ["documents/deck.pptx"],
        ["inputs/../secret.txt"],
        ["/tmp/deck.pptx"],
    ],
)
def test_rejects_invalid_required_workspace_paths(paths):
    with pytest.raises(ValueError, match="workspace_paths"):
        normalize_required_input_paths(paths)


def test_reports_missing_required_inputs(tmp_path: Path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "present.pptx").write_bytes(b"pptx")

    assert missing_required_inputs(
        tmp_path,
        ["inputs/present.pptx", "inputs/missing.pptx"],
    ) == ["inputs/missing.pptx"]
