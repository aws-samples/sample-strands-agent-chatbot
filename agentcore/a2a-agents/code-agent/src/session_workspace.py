"""Session Workspace synchronization for the Code Agent runtime."""

import logging
import shutil
import tempfile
import uuid
from pathlib import Path
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)


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
    prefix = f"code-interpreter-workspace/{user_id}/{session_id}/inputs"
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
