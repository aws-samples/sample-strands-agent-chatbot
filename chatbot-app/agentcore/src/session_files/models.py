"""Domain models for durable files owned by one chat session."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Optional


class SessionFileState(str, Enum):
    RESERVED = "RESERVED"
    UPLOADING = "UPLOADING"
    READY = "READY"
    FAILED = "FAILED"
    DELETED = "DELETED"


class SessionFileRole(str, Enum):
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"


@dataclass(frozen=True)
class BlobRef:
    backend: str
    locator: str
    version: Optional[str] = None


@dataclass(frozen=True)
class BlobMetadata:
    size_bytes: int
    checksum_sha256: str
    version: Optional[str] = None
    etag: Optional[str] = None


@dataclass(frozen=True)
class UploadTarget:
    url: str
    method: str
    headers: Dict[str, str]


@dataclass(frozen=True)
class SessionFileRef:
    file_id: str
    filename: str
    media_type: str
    artifact_type: str
    role: str
    state: str
    revision: int
    size_bytes: Optional[int] = None
    checksum_sha256: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fileId": self.file_id,
            "filename": self.filename,
            "mediaType": self.media_type,
            "artifactType": self.artifact_type,
            "role": self.role,
            "state": self.state,
            "revision": self.revision,
            **(
                {"sizeBytes": self.size_bytes}
                if self.size_bytes is not None
                else {}
            ),
            **(
                {"checksumSha256": self.checksum_sha256}
                if self.checksum_sha256
                else {}
            ),
        }


@dataclass(frozen=True)
class SessionFile:
    user_id: str
    session_id: str
    file_id: str
    filename: str
    media_type: str
    artifact_type: str
    role: SessionFileRole
    state: SessionFileState
    revision: int
    producer_tool: str
    producer_id: str
    idempotency_key: str
    created_at: str
    updated_at: str
    blob_ref: Optional[BlobRef] = None
    size_bytes: Optional[int] = None
    checksum_sha256: Optional[str] = None
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None

    @property
    def session_key(self) -> str:
        return f"USER#{self.user_id}#SESSION#{self.session_id}"

    @property
    def record_key(self) -> str:
        return f"FILE#{self.file_id}"

    def to_item(self) -> Dict[str, Any]:
        item: Dict[str, Any] = {
            "sessionKey": self.session_key,
            "recordKey": self.record_key,
            "recordType": "SESSION_FILE",
            "userId": self.user_id,
            "sessionId": self.session_id,
            "fileId": self.file_id,
            "filename": self.filename,
            "mediaType": self.media_type,
            "artifactType": self.artifact_type,
            "role": self.role.value,
            "state": self.state.value,
            "revision": self.revision,
            "producerTool": self.producer_tool,
            "producerId": self.producer_id,
            "idempotencyKey": self.idempotency_key,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }
        if self.blob_ref:
            item["blobRef"] = asdict(self.blob_ref)
        if self.size_bytes is not None:
            item["sizeBytes"] = self.size_bytes
        if self.checksum_sha256:
            item["checksumSha256"] = self.checksum_sha256
        if self.failure_code:
            item["failureCode"] = self.failure_code
        if self.failure_message:
            item["failureMessage"] = self.failure_message
        return item

    @classmethod
    def from_item(cls, item: Dict[str, Any]) -> "SessionFile":
        raw_blob_ref = item.get("blobRef")
        blob_ref = BlobRef(**raw_blob_ref) if raw_blob_ref else None
        return cls(
            user_id=item["userId"],
            session_id=item["sessionId"],
            file_id=item["fileId"],
            filename=item["filename"],
            media_type=item["mediaType"],
            artifact_type=item.get("artifactType", "file"),
            role=SessionFileRole(item["role"]),
            state=SessionFileState(item["state"]),
            revision=int(item.get("revision", 1)),
            producer_tool=item.get("producerTool", "unknown"),
            producer_id=item.get("producerId", "unknown"),
            idempotency_key=item.get("idempotencyKey", item["fileId"]),
            created_at=item["createdAt"],
            updated_at=item["updatedAt"],
            blob_ref=blob_ref,
            size_bytes=(
                int(item["sizeBytes"])
                if item.get("sizeBytes") is not None
                else None
            ),
            checksum_sha256=item.get("checksumSha256"),
            failure_code=item.get("failureCode"),
            failure_message=item.get("failureMessage"),
        )

    def to_ref(self) -> SessionFileRef:
        return SessionFileRef(
            file_id=self.file_id,
            filename=self.filename,
            media_type=self.media_type,
            artifact_type=self.artifact_type,
            role=self.role.value,
            state=self.state.value,
            revision=self.revision,
            size_bytes=self.size_bytes,
            checksum_sha256=self.checksum_sha256,
        )
