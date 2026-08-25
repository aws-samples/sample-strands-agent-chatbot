"""Manifest repository ports and DynamoDB implementation."""

from __future__ import annotations

import os
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, Optional, Protocol, Tuple

import boto3
from botocore.exceptions import ClientError

from .models import (
    BlobMetadata,
    BlobRef,
    SessionFile,
    SessionFileRole,
    SessionFileState,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_file_id(user_id: str, session_id: str, idempotency_key: str) -> str:
    source = f"session-file:{user_id}\0{session_id}\0{idempotency_key}"
    return uuid.uuid5(uuid.NAMESPACE_URL, source).hex


class SessionFileRepository(Protocol):
    def reserve(
        self,
        *,
        user_id: str,
        session_id: str,
        filename: str,
        media_type: str,
        artifact_type: str,
        role: SessionFileRole,
        producer_tool: str,
        producer_id: str,
        idempotency_key: str,
    ) -> SessionFile:
        ...

    def mark_uploading(
        self,
        session_file: SessionFile,
        blob_ref: BlobRef,
    ) -> SessionFile:
        ...

    def mark_ready(
        self,
        session_file: SessionFile,
        blob_ref: BlobRef,
        metadata: BlobMetadata,
    ) -> SessionFile:
        ...

    def mark_failed(
        self,
        session_file: SessionFile,
        *,
        code: str,
        message: str,
    ) -> SessionFile:
        ...

    def get(
        self,
        user_id: str,
        session_id: str,
        file_id: str,
    ) -> Optional[SessionFile]:
        ...


class DynamoSessionFileRepository:
    def __init__(self, table_name: str, *, resource=None):
        if not table_name:
            raise ValueError("SESSION_FILES_TABLE is required")
        region = os.getenv("AWS_REGION", "us-west-2")
        dynamodb = resource or boto3.resource("dynamodb", region_name=region)
        self.table = dynamodb.Table(table_name)

    @classmethod
    def from_environment(cls) -> "DynamoSessionFileRepository":
        return cls(os.getenv("SESSION_FILES_TABLE", "").strip())

    def reserve(
        self,
        *,
        user_id: str,
        session_id: str,
        filename: str,
        media_type: str,
        artifact_type: str,
        role: SessionFileRole,
        producer_tool: str,
        producer_id: str,
        idempotency_key: str,
    ) -> SessionFile:
        file_id = _stable_file_id(user_id, session_id, idempotency_key)
        timestamp = _now()
        session_file = SessionFile(
            user_id=user_id,
            session_id=session_id,
            file_id=file_id,
            filename=filename,
            media_type=media_type,
            artifact_type=artifact_type,
            role=role,
            state=SessionFileState.RESERVED,
            revision=1,
            producer_tool=producer_tool,
            producer_id=producer_id,
            idempotency_key=idempotency_key,
            created_at=timestamp,
            updated_at=timestamp,
        )
        try:
            self.table.put_item(
                Item=session_file.to_item(),
                ConditionExpression="attribute_not_exists(recordKey)",
            )
            return session_file
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
        existing = self.get(user_id, session_id, file_id)
        if not existing:
            raise RuntimeError("Idempotent session file reservation was lost")
        if existing.idempotency_key != idempotency_key:
            raise RuntimeError("Session file idempotency collision")
        return existing

    def mark_uploading(
        self,
        session_file: SessionFile,
        blob_ref: BlobRef,
    ) -> SessionFile:
        if session_file.state == SessionFileState.READY:
            return session_file
        timestamp = _now()
        try:
            response = self.table.update_item(
                Key={
                    "sessionKey": session_file.session_key,
                    "recordKey": session_file.record_key,
                },
                UpdateExpression=(
                    "SET #state = :uploading, blobRef = :blob, updatedAt = :updated "
                    "REMOVE failureCode, failureMessage"
                ),
                ConditionExpression="#state IN (:reserved, :failed, :uploading)",
                ExpressionAttributeNames={"#state": "state"},
                ExpressionAttributeValues={
                    ":uploading": SessionFileState.UPLOADING.value,
                    ":reserved": SessionFileState.RESERVED.value,
                    ":failed": SessionFileState.FAILED.value,
                    ":blob": {
                        "backend": blob_ref.backend,
                        "locator": blob_ref.locator,
                    },
                    ":updated": timestamp,
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            current = self.get(
                session_file.user_id,
                session_file.session_id,
                session_file.file_id,
            )
            if current and current.state == SessionFileState.READY:
                return current
            raise
        return SessionFile.from_item(response["Attributes"])

    def mark_ready(
        self,
        session_file: SessionFile,
        blob_ref: BlobRef,
        metadata: BlobMetadata,
    ) -> SessionFile:
        timestamp = _now()
        stored_blob = {
            "backend": blob_ref.backend,
            "locator": blob_ref.locator,
            **({"version": metadata.version} if metadata.version else {}),
        }
        try:
            response = self.table.update_item(
                Key={
                    "sessionKey": session_file.session_key,
                    "recordKey": session_file.record_key,
                },
                UpdateExpression=(
                    "SET #state = :ready, blobRef = :blob, sizeBytes = :size, "
                    "checksumSha256 = :checksum, updatedAt = :updated "
                    "REMOVE failureCode, failureMessage"
                ),
                ConditionExpression="#state = :uploading",
                ExpressionAttributeNames={"#state": "state"},
                ExpressionAttributeValues={
                    ":ready": SessionFileState.READY.value,
                    ":uploading": SessionFileState.UPLOADING.value,
                    ":blob": stored_blob,
                    ":size": metadata.size_bytes,
                    ":checksum": metadata.checksum_sha256,
                    ":updated": timestamp,
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            current = self.get(
                session_file.user_id,
                session_file.session_id,
                session_file.file_id,
            )
            if current and current.state == SessionFileState.READY:
                return current
            raise
        return SessionFile.from_item(response["Attributes"])

    def mark_failed(
        self,
        session_file: SessionFile,
        *,
        code: str,
        message: str,
    ) -> SessionFile:
        timestamp = _now()
        response = self.table.update_item(
            Key={
                "sessionKey": session_file.session_key,
                "recordKey": session_file.record_key,
            },
            UpdateExpression=(
                "SET #state = :failed, failureCode = :code, "
                "failureMessage = :message, updatedAt = :updated"
            ),
            ConditionExpression="#state <> :ready",
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":failed": SessionFileState.FAILED.value,
                ":ready": SessionFileState.READY.value,
                ":code": code[:120],
                ":message": message[:1000],
                ":updated": timestamp,
            },
            ReturnValues="ALL_NEW",
        )
        return SessionFile.from_item(response["Attributes"])

    def get(
        self,
        user_id: str,
        session_id: str,
        file_id: str,
    ) -> Optional[SessionFile]:
        response = self.table.get_item(
            Key={
                "sessionKey": f"USER#{user_id}#SESSION#{session_id}",
                "recordKey": f"FILE#{file_id}",
            },
            ConsistentRead=True,
        )
        item = response.get("Item")
        return SessionFile.from_item(item) if item else None


class InMemorySessionFileRepository:
    """Explicit test/local repository implementing the same state contract."""

    def __init__(self):
        self._items: Dict[Tuple[str, str, str], SessionFile] = {}
        self._lock = Lock()

    @staticmethod
    def _key(user_id: str, session_id: str, file_id: str):
        return user_id, session_id, file_id

    def reserve(self, **kwargs) -> SessionFile:
        file_id = _stable_file_id(
            kwargs["user_id"],
            kwargs["session_id"],
            kwargs["idempotency_key"],
        )
        key = self._key(kwargs["user_id"], kwargs["session_id"], file_id)
        with self._lock:
            existing = self._items.get(key)
            if existing:
                return existing
            timestamp = _now()
            item = SessionFile(
                file_id=file_id,
                state=SessionFileState.RESERVED,
                revision=1,
                created_at=timestamp,
                updated_at=timestamp,
                **kwargs,
            )
            self._items[key] = item
            return item

    def mark_uploading(
        self,
        session_file: SessionFile,
        blob_ref: BlobRef,
    ) -> SessionFile:
        if session_file.state == SessionFileState.READY:
            return session_file
        updated = replace(
            session_file,
            state=SessionFileState.UPLOADING,
            blob_ref=blob_ref,
            updated_at=_now(),
            failure_code=None,
            failure_message=None,
        )
        self._items[self._key(updated.user_id, updated.session_id, updated.file_id)] = updated
        return updated

    def mark_ready(
        self,
        session_file: SessionFile,
        blob_ref: BlobRef,
        metadata: BlobMetadata,
    ) -> SessionFile:
        updated = replace(
            session_file,
            state=SessionFileState.READY,
            blob_ref=replace(blob_ref, version=metadata.version),
            size_bytes=metadata.size_bytes,
            checksum_sha256=metadata.checksum_sha256,
            updated_at=_now(),
            failure_code=None,
            failure_message=None,
        )
        self._items[self._key(updated.user_id, updated.session_id, updated.file_id)] = updated
        return updated

    def mark_failed(
        self,
        session_file: SessionFile,
        *,
        code: str,
        message: str,
    ) -> SessionFile:
        updated = replace(
            session_file,
            state=SessionFileState.FAILED,
            failure_code=code,
            failure_message=message,
            updated_at=_now(),
        )
        self._items[self._key(updated.user_id, updated.session_id, updated.file_id)] = updated
        return updated

    def get(
        self,
        user_id: str,
        session_id: str,
        file_id: str,
    ) -> Optional[SessionFile]:
        return self._items.get(self._key(user_id, session_id, file_id))
