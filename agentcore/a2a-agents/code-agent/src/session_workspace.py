"""Session Workspace synchronization for the Code Agent runtime."""

import hashlib
import logging
import shutil
import tempfile
import uuid
from pathlib import Path
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)


def code_interpreter_workspace_id(user_id: str, session_id: str) -> str:
    """Return the canonical opaque workspace ID shared with the orchestrator."""
    source = f"{user_id}\0{session_id}".encode("utf-8")
    return hashlib.sha256(source).hexdigest()[:48]


def code_interpreter_input_prefix(user_id: str, session_id: str) -> str:
    workspace_id = code_interpreter_workspace_id(user_id, session_id)
    return f"code-interpreter-workspace/{workspace_id}/inputs"


def _remove_local_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _safe_relative_path(raw_path: str) -> Path:
    path = PurePosixPath(raw_path)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"Unsafe workspace object path: {raw_path!r}")
    return Path(*path.parts)


def restore_s3_prefix(
    local_dir: Path,
    bucket: str,
    s3_prefix: str,
    s3_client,
    *,
    exclude_top_level_inputs: bool = False,
    excluded_parts: set[str] | None = None,
    strict: bool = False,
) -> int:
    """Download one S3 prefix into a local directory."""
    local_dir.mkdir(parents=True, exist_ok=True)
    local_root = local_dir.resolve()
    paginator = s3_client.get_paginator("list_objects_v2")
    downloaded = 0
    try:
        for page in paginator.paginate(Bucket=bucket, Prefix=s3_prefix + "/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/"):
                    continue
                rel = key[len(s3_prefix) + 1:]
                if not rel:
                    continue
                relative_path = _safe_relative_path(rel)
                if exclude_top_level_inputs and relative_path.parts[0] == "inputs":
                    continue
                if excluded_parts and any(
                    part in excluded_parts for part in relative_path.parts
                ):
                    continue
                dest = local_dir / relative_path
                if not dest.resolve(strict=False).is_relative_to(local_root):
                    raise ValueError(
                        f"Workspace object escapes destination: {key!r}"
                    )
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    s3_client.download_file(bucket, key, str(dest))
                    downloaded += 1
                except Exception as error:
                    if strict:
                        raise
                    logger.warning("[S3 restore] Failed to download %s: %s", key, error)
    except Exception as error:
        if strict:
            raise
        logger.warning(
            "[S3 restore] List failed for prefix '%s': %s",
            s3_prefix,
            error,
        )
    return downloaded


def sync_session_inputs(
    bucket: str,
    user_id: str,
    session_id: str,
    workspace: Path,
    s3_client,
) -> list[str]:
    """Atomically mirror the canonical session upload prefix under workspace/inputs."""
    workspace.mkdir(parents=True, exist_ok=True)
    inputs_dir = workspace / "inputs"
    staging_dir = Path(
        tempfile.mkdtemp(prefix=".inputs-sync-", dir=workspace)
    )
    backup_dir = workspace / f".inputs-backup-{uuid.uuid4().hex}"
    prefix = code_interpreter_input_prefix(user_id, session_id)
    try:
        downloaded = restore_s3_prefix(
            staging_dir,
            bucket,
            prefix,
            s3_client,
            strict=True,
        )

        had_existing_path = inputs_dir.exists() and not inputs_dir.is_symlink()
        if inputs_dir.is_symlink():
            inputs_dir.unlink()
        elif had_existing_path:
            inputs_dir.rename(backup_dir)

        try:
            staging_dir.rename(inputs_dir)
        except Exception:
            if had_existing_path and backup_dir.exists():
                backup_dir.rename(inputs_dir)
            raise

        _remove_local_path(backup_dir)
        files = sorted(
            path.relative_to(workspace).as_posix()
            for path in inputs_dir.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        logger.info("[Workspace inputs] Synced %s file(s)", downloaded)
        return [f"- `{path}`" for path in files]
    finally:
        _remove_local_path(staging_dir)


def normalize_required_input_paths(raw_paths: object) -> list[str]:
    """Normalize delegated Workspace paths to Code Agent-local input paths."""
    if not isinstance(raw_paths, list):
        raise ValueError("workspace_paths must be a list")

    normalized: list[str] = []
    for raw_path in raw_paths:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("workspace_paths entries must be non-empty strings")

        value = raw_path.strip()
        mount_prefix = "/mnt/workspace/"
        if value.startswith(mount_prefix):
            value = value[len(mount_prefix):]

        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or len(path.parts) < 2
            or path.parts[0] not in {"inputs", "uploads"}
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError(
                "workspace_paths entries must be under inputs/ or uploads/"
            )

        local_path = PurePosixPath("inputs", *path.parts[1:]).as_posix()
        if local_path not in normalized:
            normalized.append(local_path)
    return normalized


def missing_required_inputs(workspace: Path, required_paths: list[str]) -> list[str]:
    """Return required input paths that are absent from the synchronized mirror."""
    workspace_root = workspace.resolve()
    missing = []
    for relative_path in required_paths:
        candidate = workspace / Path(*PurePosixPath(relative_path).parts)
        resolved = candidate.resolve(strict=False)
        if (
            not resolved.is_relative_to(workspace_root)
            or candidate.is_symlink()
            or not candidate.is_file()
        ):
            missing.append(relative_path)
    return missing
