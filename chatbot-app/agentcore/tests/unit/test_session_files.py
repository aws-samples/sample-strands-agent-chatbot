import json
from unittest.mock import MagicMock

import pytest

from session_files.models import (
    BlobMetadata,
    BlobRef,
    SessionFileRole,
    SessionFileState,
    UploadTarget,
)
from session_files.publisher import PublishError, SessionFilePublisher
from session_files.repository import InMemorySessionFileRepository


def _response(stdout: str = "", *, error: str = ""):
    return {
        "stream": [{
            "result": {
                "structuredContent": {
                    "stdout": stdout,
                    "stderr": error,
                },
                "isError": bool(error),
            },
        }],
    }


class FakeBlobStore:
    backend = "fake"

    def __init__(self):
        self.allocated = []
        self.verified = []

    def allocate(self, session_file):
        ref = BlobRef("fake", f"blob/{session_file.file_id}")
        self.allocated.append(ref)
        return ref

    def create_upload_target(
        self,
        blob_ref,
        *,
        media_type,
        checksum_sha256_base64,
    ):
        return UploadTarget(
            url="https://blob.example/upload",
            method="PUT",
            headers={
                "Content-Type": media_type,
                "x-checksum": checksum_sha256_base64,
            },
        )

    def verify(
        self,
        blob_ref,
        *,
        expected_size,
        expected_checksum_sha256_base64,
    ):
        self.verified.append(blob_ref)
        return BlobMetadata(
            size_bytes=expected_size,
            checksum_sha256=expected_checksum_sha256_base64,
            version="v1",
        )


def test_publisher_commits_ready_file_with_stable_id():
    repository = InMemorySessionFileRepository()
    blob_store = FakeBlobStore()
    publisher = SessionFilePublisher(repository, blob_store)
    ci = MagicMock()
    ci.invoke.side_effect = [
        _response(json.dumps({
            "sizeBytes": 12,
            "checksumSha256": "hex",
            "checksumSha256Base64": "base64",
        })),
        _response(json.dumps({"status": 200, "sizeBytes": 12})),
    ]

    result = publisher.publish_code_interpreter_file(
        code_interpreter=ci,
        source_path="/mnt/workspace/report.pdf",
        user_id="user1",
        session_id="session1",
        filename="report.pdf",
        media_type="application/pdf",
        artifact_type="document",
        producer_tool="execute_code",
        producer_id="tool-1",
        idempotency_key="tool-1:0",
    )

    assert result.state == "READY"
    assert result.size_bytes == 12
    stored = repository.get("user1", "session1", result.file_id)
    assert stored is not None
    assert stored.state == SessionFileState.READY
    assert stored.blob_ref == BlobRef(
        backend="fake",
        locator=f"blob/{result.file_id}",
        version="v1",
    )

    repeated = publisher.publish_code_interpreter_file(
        code_interpreter=ci,
        source_path="/mnt/workspace/report.pdf",
        user_id="user1",
        session_id="session1",
        filename="report.pdf",
        media_type="application/pdf",
        artifact_type="document",
        producer_tool="execute_code",
        producer_id="tool-1",
        idempotency_key="tool-1:0",
    )
    assert repeated.file_id == result.file_id
    assert ci.invoke.call_count == 2


def test_publisher_marks_failed_without_storage_fallback():
    repository = InMemorySessionFileRepository()
    publisher = SessionFilePublisher(repository, FakeBlobStore())
    ci = MagicMock()
    ci.invoke.return_value = _response(error="network denied")

    with pytest.raises(PublishError, match="network denied"):
        publisher.publish_code_interpreter_file(
            code_interpreter=ci,
            source_path="/mnt/workspace/report.pdf",
            user_id="user1",
            session_id="session1",
            filename="report.pdf",
            media_type="application/pdf",
            artifact_type="document",
            producer_tool="execute_code",
            producer_id="tool-2",
            idempotency_key="tool-2:0",
        )

    failed = repository.reserve(
        user_id="user1",
        session_id="session1",
        filename="report.pdf",
        media_type="application/pdf",
        artifact_type="document",
        role=SessionFileRole.OUTPUT,
        producer_tool="execute_code",
        producer_id="tool-2",
        idempotency_key="tool-2:0",
    )
    assert failed.state == SessionFileState.FAILED
