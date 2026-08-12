"""Session-scoped S3 Files access points for Code Interpreter."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Dict, Optional

import boto3

from workspace.paths import code_interpreter_prefix

logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_STATE_ACCESS_POINT_ID = "ci_workspace_access_point_v2_id"
_STATE_ACCESS_POINT_ARN = "ci_workspace_access_point_v2_arn"


def get_s3_files_configuration() -> Optional[Dict[str, str]]:
    file_system_id = os.getenv("S3_FILES_FILE_SYSTEM_ID", "").strip()
    file_system_arn = os.getenv("S3_FILES_FILE_SYSTEM_ARN", "").strip()
    mount_path = os.getenv("S3_FILES_MOUNT_PATH", "/mnt/workspace").strip()
    if not file_system_id or not file_system_arn:
        return None
    return {
        "file_system_id": file_system_id,
        "file_system_arn": file_system_arn,
        "mount_path": mount_path or "/mnt/workspace",
    }


def _validate_identity(value: str, label: str) -> None:
    if not value or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"Invalid {label} for S3 Files workspace")


def _wait_for_access_point(client: Any, access_point_id: str) -> Dict[str, Any]:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        response = client.get_access_point(accessPointId=access_point_id)
        status = str(response.get("status", "")).upper()
        if status == "AVAILABLE":
            return response
        if status in {"FAILED", "ERROR", "DELETED"}:
            raise RuntimeError(
                f"S3 Files access point {access_point_id} entered {status}"
            )
        time.sleep(2)
    raise TimeoutError(f"S3 Files access point {access_point_id} was not ready")


def _persist_access_point_registry(
    user_id: str,
    session_id: str,
    access_point_id: str,
    access_point_arn: str,
) -> None:
    from workspace.config import get_workspace_bucket

    region = os.getenv("AWS_REGION", "us-west-2")
    boto3.client("s3", region_name=region).put_object(
        Bucket=get_workspace_bucket(),
        Key=f".workspace-access-points/{user_id}/{session_id}.json",
        Body=json.dumps({
            "accessPointId": access_point_id,
            "accessPointArn": access_point_arn,
        }).encode("utf-8"),
        ContentType="application/json",
    )


def get_or_create_session_access_point(
    agent_state: Any,
    user_id: str,
    session_id: str,
) -> Dict[str, str]:
    config = get_s3_files_configuration()
    if not config:
        raise RuntimeError(
            "S3 Files workspace is not configured: "
            "S3_FILES_FILE_SYSTEM_ID and S3_FILES_FILE_SYSTEM_ARN are required"
        )

    _validate_identity(user_id, "user_id")
    _validate_identity(session_id, "session_id")

    region = os.getenv("AWS_REGION", "us-west-2")
    client = boto3.client("s3files", region_name=region)
    stored_id = agent_state.get(_STATE_ACCESS_POINT_ID)
    stored_arn = agent_state.get(_STATE_ACCESS_POINT_ARN)

    if stored_id and stored_arn:
        try:
            response = client.get_access_point(accessPointId=stored_id)
            if str(response.get("status", "")).upper() == "AVAILABLE":
                _persist_access_point_registry(
                    user_id,
                    session_id,
                    stored_id,
                    stored_arn,
                )
                return {
                    **config,
                    "access_point_id": stored_id,
                    "access_point_arn": stored_arn,
                }
        except Exception as error:
            logger.info("Stored S3 Files access point is unavailable: %s", error)

    token_source = (
        f"{config['file_system_id']}:{user_id}:{session_id}:code-interpreter"
    )
    client_token = hashlib.sha256(token_source.encode("utf-8")).hexdigest()
    root_path = f"/{code_interpreter_prefix(user_id, session_id).rstrip('/')}"
    response = client.create_access_point(
        clientToken=client_token,
        fileSystemId=config["file_system_id"],
        posixUser={"uid": 1000, "gid": 1000},
        rootDirectory={
            "path": root_path,
            "creationPermissions": {
                "ownerUid": 1000,
                "ownerGid": 1000,
                "permissions": "0750",
            },
        },
    )
    access_point_id = response["accessPointId"]
    ready = _wait_for_access_point(client, access_point_id)
    access_point_arn = ready.get("accessPointArn") or response["accessPointArn"]
    agent_state.set(_STATE_ACCESS_POINT_ID, access_point_id)
    agent_state.set(_STATE_ACCESS_POINT_ARN, access_point_arn)
    _persist_access_point_registry(
        user_id,
        session_id,
        access_point_id,
        access_point_arn,
    )
    logger.info(
        "Created S3 Files access point %s for session workspace",
        access_point_id,
    )
    return {
        **config,
        "access_point_id": access_point_id,
        "access_point_arn": access_point_arn,
    }
