"""Blob storage ports and the AWS S3 adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import boto3

from workspace.paths import code_interpreter_workspace_id

from .models import BlobMetadata, BlobRef, SessionFile, UploadTarget


class BlobStore(Protocol):
    backend: str

    def allocate(self, session_file: SessionFile) -> BlobRef:
        """Allocate an immutable provider locator for a file revision."""

    def create_upload_target(
        self,
        blob_ref: BlobRef,
        *,
        media_type: str,
        checksum_sha256_base64: str,
    ) -> UploadTarget:
        """Create a short-lived target used by an isolated producer."""

    def verify(
        self,
        blob_ref: BlobRef,
        *,
        expected_size: int,
        expected_checksum_sha256_base64: str,
    ) -> BlobMetadata:
        """Verify the committed blob before the manifest becomes READY."""


@dataclass
class S3BlobStore:
    bucket: str
    region: str
    client: object
    backend: str = "s3"

    @classmethod
    def from_environment(cls) -> "S3BlobStore":
        bucket = os.getenv("ARTIFACT_BUCKET", "").strip()
        if not bucket:
            raise ValueError("ARTIFACT_BUCKET is required")
        region = os.getenv("AWS_REGION", "us-west-2")
        return cls(
            bucket=bucket,
            region=region,
            client=boto3.client("s3", region_name=region),
        )

    @staticmethod
    def _safe_filename(filename: str) -> str:
        if (
            not filename
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
            or "\0" in filename
        ):
            raise ValueError("Invalid session file name")
        return filename

    def allocate(self, session_file: SessionFile) -> BlobRef:
        workspace_id = code_interpreter_workspace_id(
            session_file.user_id,
            session_file.session_id,
        )
        filename = self._safe_filename(session_file.filename)
        locator = (
            f"session-files/{workspace_id}/outputs/"
            f"{session_file.file_id}/r{session_file.revision:06d}/{filename}"
        )
        return BlobRef(backend=self.backend, locator=locator)

    def create_upload_target(
        self,
        blob_ref: BlobRef,
        *,
        media_type: str,
        checksum_sha256_base64: str,
    ) -> UploadTarget:
        if blob_ref.backend != self.backend:
            raise ValueError(f"Unsupported blob backend: {blob_ref.backend}")
        params = {
            "Bucket": self.bucket,
            "Key": blob_ref.locator,
            "ContentType": media_type,
            "ChecksumSHA256": checksum_sha256_base64,
        }
        url = self.client.generate_presigned_url(
            "put_object",
            Params=params,
            ExpiresIn=900,
            HttpMethod="PUT",
        )
        return UploadTarget(
            url=url,
            method="PUT",
            headers={
                "Content-Type": media_type,
                "x-amz-checksum-sha256": checksum_sha256_base64,
            },
        )

    def verify(
        self,
        blob_ref: BlobRef,
        *,
        expected_size: int,
        expected_checksum_sha256_base64: str,
    ) -> BlobMetadata:
        if blob_ref.backend != self.backend:
            raise ValueError(f"Unsupported blob backend: {blob_ref.backend}")
        response = self.client.head_object(
            Bucket=self.bucket,
            Key=blob_ref.locator,
            ChecksumMode="ENABLED",
        )
        actual_size = int(response.get("ContentLength", -1))
        actual_checksum = response.get("ChecksumSHA256")
        if actual_size != expected_size:
            raise ValueError(
                f"Published file size mismatch: expected {expected_size}, "
                f"received {actual_size}"
            )
        if actual_checksum != expected_checksum_sha256_base64:
            raise ValueError("Published file checksum mismatch")
        return BlobMetadata(
            size_bytes=actual_size,
            checksum_sha256=expected_checksum_sha256_base64,
            version=response.get("VersionId"),
            etag=response.get("ETag"),
        )


def blob_store_from_environment() -> BlobStore:
    backend = os.getenv("SESSION_FILE_BLOB_BACKEND", "").strip()
    if backend == "s3":
        return S3BlobStore.from_environment()
    if not backend:
        raise ValueError("SESSION_FILE_BLOB_BACKEND is required")
    raise ValueError(f"Unsupported session file blob backend: {backend}")
