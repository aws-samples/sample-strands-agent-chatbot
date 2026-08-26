"""
Document Managers - Specific implementations for each document type

Provides specialized managers for Word, Excel, PowerPoint, and Image files.
Each manager inherits from BaseDocumentManager and adds type-specific functionality.
"""

import hashlib
import json
import logging
import re
from typing import List, Dict, Any, Optional

from botocore.exceptions import ClientError

from .base_manager import BaseDocumentManager
from .paths import code_interpreter_prefix

logger = logging.getLogger(__name__)


class WordManager(BaseDocumentManager):
    """Document manager specifically for Word (.docx) files"""

    def __init__(self, user_id: str, session_id: str):
        super().__init__(user_id, session_id, document_type='word')
        logger.info("WordManager initialized")

    def validate_docx_filename(self, filename: str) -> bool:
        """Validate that filename ends with .docx"""
        if not filename.endswith('.docx'):
            raise ValueError(f"Filename must end with .docx: {filename}")
        return True

    def format_file_list(self, documents: List[Dict[str, Any]]) -> str:
        """Format document list for display

        Args:
            documents: List of document info dicts from list_s3_documents()

        Returns:
            Formatted string for display
        """
        if not documents:
            return "**Workspace**: Empty (no documents yet)"

        lines = [f"**Workspace** ({len(documents)} document{'s' if len(documents) > 1 else ''}):"]

        for doc in sorted(documents, key=lambda x: x['last_modified'], reverse=True):
            # Parse ISO timestamp
            modified_date = doc['last_modified'].split('T')[0]
            lines.append(f"  - **{doc['filename']}** ({doc['size_kb']}) - Modified: {modified_date}")

        return "\n".join(lines)


class ExcelManager(BaseDocumentManager):
    """Document manager for Excel (.xlsx) files"""

    def __init__(self, user_id: str, session_id: str):
        super().__init__(user_id, session_id, document_type='excel')
        logger.info("ExcelManager initialized")

    def validate_xlsx_filename(self, filename: str) -> bool:
        """Validate that filename ends with .xlsx"""
        if not filename.endswith('.xlsx'):
            raise ValueError(f"Filename must end with .xlsx: {filename}")
        return True

    def format_file_list(self, documents: List[Dict[str, Any]]) -> str:
        """Format spreadsheet list for display

        Args:
            documents: List of document info dicts from list_s3_documents()

        Returns:
            Formatted string for display
        """
        if not documents:
            return "**Workspace**: Empty (no spreadsheets yet)"

        lines = [f"**Workspace** ({len(documents)} spreadsheet{'s' if len(documents) > 1 else ''}):"]

        for doc in sorted(documents, key=lambda x: x['last_modified'], reverse=True):
            # Parse ISO timestamp
            modified_date = doc['last_modified'].split('T')[0]
            lines.append(f"  - **{doc['filename']}** ({doc['size_kb']}) - Modified: {modified_date}")

        return "\n".join(lines)


class PowerPointManager(BaseDocumentManager):
    """PowerPoint view over the canonical session Workspace.

    Uploaded sources are immutable objects under ``inputs/``. Published files
    live under ``artifacts/powerpoint/`` and edit drafts are hidden under
    ``.drafts/powerpoint/``. The legacy ``documents/.../powerpoint`` namespace
    is deliberately not read.
    """

    _EDIT_ID = re.compile(r"^edit-[a-f0-9]{24}$")
    _CONTENT_TYPE = (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )

    def __init__(self, user_id: str, session_id: str):
        super().__init__(user_id, session_id, document_type='powerpoint')
        workspace_prefix = code_interpreter_prefix(user_id, session_id).rstrip("/")
        self.input_prefix = f"{workspace_prefix}/inputs"
        self.artifact_prefix = f"{workspace_prefix}/artifacts/powerpoint"
        self.draft_prefix = f"{workspace_prefix}/.drafts/powerpoint"
        self.metadata_prefix = f"{workspace_prefix}/.metadata/powerpoint"
        self.s3_prefix = self.artifact_prefix
        logger.info("PowerPointManager initialized")

    @staticmethod
    def _is_missing(error: Exception) -> bool:
        if isinstance(error, ClientError):
            return error.response.get("Error", {}).get("Code") in {
                "404",
                "NoSuchKey",
                "NotFound",
            }
        return error.__class__.__name__ == "NoSuchKey"

    def _head(self, key: str) -> Optional[Dict[str, Any]]:
        try:
            return self.s3_client.head_object(Bucket=self.bucket, Key=key)
        except self.s3_client.exceptions.NoSuchKey:
            return None
        except Exception as error:
            if self._is_missing(error):
                return None
            raise

    def _get(self, key: str) -> tuple[bytes, Dict[str, Any]]:
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
        except self.s3_client.exceptions.NoSuchKey as error:
            raise FileNotFoundError(f"Presentation not found: {key}") from error
        except Exception as error:
            if self._is_missing(error):
                raise FileNotFoundError(f"Presentation not found: {key}") from error
            raise
        return response["Body"].read(), response

    def _presentation_candidates(self, filename: str) -> list[str]:
        return [
            f"{self.artifact_prefix}/{filename}",
            f"{self.input_prefix}/{filename}",
        ]

    def resolve_presentation(self, filename: str) -> Dict[str, Any]:
        """Resolve one public filename without compatibility fallbacks."""
        if filename.endswith(".pptx") and self._EDIT_ID.fullmatch(filename[:-5]):
            edit_id = filename[:-5]
            draft_key = f"{self.draft_prefix}/{edit_id}.pptx"
            head = self._head(draft_key)
            if head is None:
                raise FileNotFoundError(f"Edit draft not found: {edit_id}")
            return {
                "filename": filename,
                "key": draft_key,
                "etag": head.get("ETag"),
                "scope": "draft",
            }

        matches = []
        for scope, key in zip(
            ("artifact", "input"),
            self._presentation_candidates(filename),
        ):
            head = self._head(key)
            if head is not None:
                matches.append({
                    "filename": filename,
                    "key": key,
                    "etag": head.get("ETag"),
                    "scope": scope,
                })
        if not matches:
            raise FileNotFoundError(f"Presentation not found: {filename}")
        if len(matches) > 1:
            raise ValueError(
                f"Presentation name is ambiguous in Workspace: {filename}. "
                "Rename the output so it does not shadow an uploaded source."
            )
        return matches[0]

    def save_input(
        self,
        filename: str,
        file_bytes: bytes,
        metadata: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Store an immutable user-provided source in canonical Workspace inputs."""
        self.validate_pptx_filename(filename)
        key = f"{self.input_prefix}/{filename}"
        s3_metadata = {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "document_type": self.document_type,
            "source": "user_upload",
            **(metadata or {}),
        }
        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=file_bytes,
            Metadata=s3_metadata,
            ContentType=self._CONTENT_TYPE,
        )
        return self._save_result(key, file_bytes, self.bucket)

    @staticmethod
    def _save_result(key: str, file_bytes: bytes, bucket: str = "") -> Dict[str, str]:
        size_kb = len(file_bytes) / 1024
        return {
            "s3_key": key,
            "s3_url": f"s3://{bucket}/{key}" if bucket else key,
            "size": len(file_bytes),
            "size_kb": f"{size_kb:.1f} KB",
        }

    def save_to_s3(
        self,
        filename: str,
        file_bytes: bytes,
        metadata: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Publish a PPTX or persist hidden PowerPoint metadata."""
        if filename.lower().endswith(".pptx"):
            self.validate_pptx_filename(filename)
            key = f"{self.artifact_prefix}/{filename}"
            content_type = self._CONTENT_TYPE
        else:
            key = f"{self.metadata_prefix}/{filename}"
            content_type = "application/json"
        s3_metadata = {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "document_type": self.document_type,
            **(metadata or {}),
        }
        put_args = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": file_bytes,
            "Metadata": s3_metadata,
            "ContentType": content_type,
        }
        if filename.lower().endswith(".pptx"):
            put_args["IfNoneMatch"] = "*"
        self.s3_client.put_object(**put_args)
        result = self._save_result(key, file_bytes, self.bucket)
        logger.info("Saved PowerPoint Workspace object: %s", key)
        return result

    def load_from_s3(self, filename: str) -> bytes:
        """Load a public PPTX, draft reference, or hidden metadata object."""
        if filename.lower().endswith(".pptx"):
            resolved = self.resolve_presentation(filename)
            data, _ = self._get(resolved["key"])
            return data
        data, _ = self._get(f"{self.metadata_prefix}/{filename}")
        return data

    def list_s3_documents(self) -> List[Dict[str, Any]]:
        """Return uploaded sources and published outputs, excluding drafts."""
        documents: Dict[str, Dict[str, Any]] = {}
        for scope, prefix in (
            ("input", self.input_prefix),
            ("artifact", self.artifact_prefix),
        ):
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix + "/",
            )
            for obj in response.get("Contents", []):
                filename = obj["Key"][len(prefix) + 1:]
                if "/" in filename or not filename.lower().endswith(".pptx"):
                    continue
                existing = documents.get(filename)
                if existing:
                    existing["ambiguous"] = True
                    continue
                documents[filename] = {
                    "filename": filename,
                    "size": obj["Size"],
                    "size_kb": f"{obj['Size'] / 1024:.1f} KB",
                    "last_modified": obj["LastModified"].isoformat(),
                    "s3_key": obj["Key"],
                    "scope": scope,
                }
        return list(documents.values())

    def begin_edit(self, source_filename: str, restart: bool = False) -> Dict[str, Any]:
        """Create or reuse the single hidden draft for a source presentation."""
        source = self.resolve_presentation(source_filename)
        source_bytes, source_response = self._get(source["key"])
        source_etag = source_response.get("ETag") or source["etag"]
        edit_id = "edit-" + hashlib.sha256(
            source["key"].encode("utf-8")
        ).hexdigest()[:24]
        draft_key = f"{self.draft_prefix}/{edit_id}.pptx"
        state_key = f"{self.draft_prefix}/{edit_id}.json"

        if not restart:
            try:
                state_bytes, _ = self._get(state_key)
                state = json.loads(state_bytes.decode("utf-8"))
                draft_head = self._head(draft_key)
                if (
                    draft_head is not None
                    and state.get("source_key") == source["key"]
                    and state.get("source_etag") == source_etag
                ):
                    return {
                        **state,
                        "edit_id": edit_id,
                        "draft_etag": draft_head.get("ETag"),
                        "reused": True,
                    }
            except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
                pass

        state = {
            "edit_id": edit_id,
            "source_filename": source_filename,
            "source_key": source["key"],
            "source_etag": source_etag,
        }
        temporary_tag = "lifecycle=temporary-ppt-draft"
        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=draft_key,
            Body=source_bytes,
            ContentType=self._CONTENT_TYPE,
            Tagging=temporary_tag,
        )
        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=state_key,
            Body=json.dumps(state).encode("utf-8"),
            ContentType="application/json",
            Tagging=temporary_tag,
        )
        draft_head = self._head(draft_key)
        return {
            **state,
            "draft_etag": draft_head.get("ETag") if draft_head else None,
            "reused": False,
        }

    def load_edit(self, edit_id: str) -> tuple[bytes, Dict[str, Any]]:
        if not self._EDIT_ID.fullmatch(edit_id):
            raise ValueError("Invalid PowerPoint edit_id")
        state_bytes, _ = self._get(f"{self.draft_prefix}/{edit_id}.json")
        state = json.loads(state_bytes.decode("utf-8"))
        draft_bytes, response = self._get(
            f"{self.draft_prefix}/{edit_id}.pptx"
        )
        state["draft_etag"] = response.get("ETag")
        return draft_bytes, state

    def save_edit(
        self,
        edit_id: str,
        file_bytes: bytes,
        expected_etag: str,
    ) -> str:
        if not self._EDIT_ID.fullmatch(edit_id):
            raise ValueError("Invalid PowerPoint edit_id")
        try:
            response = self.s3_client.put_object(
                Bucket=self.bucket,
                Key=f"{self.draft_prefix}/{edit_id}.pptx",
                Body=file_bytes,
                ContentType=self._CONTENT_TYPE,
                IfMatch=expected_etag,
                Tagging="lifecycle=temporary-ppt-draft",
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {
                "412",
                "PreconditionFailed",
            }:
                raise RuntimeError(
                    "The PowerPoint draft changed during this operation. "
                    "Reload the edit and retry."
                ) from error
            raise
        return response.get("ETag", "")

    def discard_edit(self, edit_id: str) -> None:
        if not self._EDIT_ID.fullmatch(edit_id):
            raise ValueError("Invalid PowerPoint edit_id")
        self.s3_client.delete_objects(
            Bucket=self.bucket,
            Delete={
                "Objects": [
                    {"Key": f"{self.draft_prefix}/{edit_id}.pptx"},
                    {"Key": f"{self.draft_prefix}/{edit_id}.json"},
                ],
                "Quiet": True,
            },
        )

    def validate_pptx_filename(self, filename: str) -> bool:
        """Validate that filename ends with .pptx

        Args:
            filename: Filename to validate

        Returns:
            True if valid

        Raises:
            ValueError: If filename doesn't end with .pptx
        """
        if not filename.lower().endswith('.pptx'):
            raise ValueError(f"Filename must end with .pptx: {filename}")
        return True

    def format_file_list(self, documents: List[Dict[str, Any]]) -> str:
        """Format presentation list for display

        Args:
            documents: List of document info dicts from list_s3_documents()

        Returns:
            Formatted string for display
        """
        if not documents:
            return "**Workspace**: Empty (no presentations yet)"

        lines = [f"**Workspace** ({len(documents)} presentation{'s' if len(documents) > 1 else ''}):"]

        for doc in sorted(documents, key=lambda x: x['last_modified'], reverse=True):
            # Parse ISO timestamp
            modified_date = doc['last_modified'].split('T')[0]
            lines.append(f"  - **{doc['filename']}** ({doc['size_kb']}) - Modified: {modified_date}")

        return "\n".join(lines)

    def save_template_metadata(self, template_info: dict, source_filename: str) -> str:
        """Save template analysis as JSON metadata in S3

        Args:
            template_info: Template analysis result (layouts, theme, etc.)
            source_filename: Source PPT filename (e.g., "company-template.pptx")

        Returns:
            S3 key of saved metadata
        """
        # Create metadata filename with dot prefix (hidden file pattern)
        metadata_filename = f".template-{source_filename}.json"
        metadata_bytes = json.dumps(template_info, indent=2).encode('utf-8')

        # Save to S3 with metadata
        s3_info = self.save_to_s3(
            metadata_filename,
            metadata_bytes,
            metadata={'type': 'template_metadata', 'source': source_filename}
        )

        logger.info(f"Saved template metadata: {metadata_filename}")
        return s3_info['s3_key']

    def load_template_metadata(self, source_filename: str) -> Optional[dict]:
        """Load template metadata if exists

        Args:
            source_filename: Source PPT filename (e.g., "company-template.pptx")

        Returns:
            Template metadata dict or None if not found
        """
        metadata_filename = f".template-{source_filename}.json"

        try:
            metadata_bytes = self.load_from_s3(metadata_filename)
            template_info = json.loads(metadata_bytes.decode('utf-8'))
            logger.info(f"Loaded template metadata for {source_filename}")
            return template_info
        except FileNotFoundError:
            logger.info(f"No template metadata found for {source_filename}")
            return None
        except Exception as e:
            logger.error(f"Failed to load template metadata: {e}")
            return None

    def get_available_templates(self) -> List[str]:
        """List all presentations that have template metadata

        Returns:
            List of presentation filenames that can be used as templates
        """
        all_docs = self.list_s3_documents()
        templates = []

        for doc in all_docs:
            if doc['filename'].endswith('.pptx'):
                # Check if template metadata exists
                metadata_filename = f".template-{doc['filename']}.json"
                try:
                    self.load_from_s3(metadata_filename)
                    templates.append(doc['filename'])
                except:  # noqa: E722
                    pass

        logger.info(f"Found {len(templates)} available templates")
        return templates


class ImageManager(BaseDocumentManager):
    """Document manager for image files (.png, .jpg, .jpeg, .gif, .webp)"""

    def __init__(self, user_id: str, session_id: str):
        super().__init__(user_id, session_id, document_type='image')
        logger.info("ImageManager initialized")

    def validate_image_filename(self, filename: str) -> bool:
        """Validate that filename is a supported image format"""
        valid_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.pdf')
        if not filename.lower().endswith(valid_extensions):
            raise ValueError(f"Filename must be a supported image/document format: {filename}")
        return True

    def get_image_mime_type(self, filename: str) -> str:
        """Get MIME type for image based on extension"""
        extension = filename.lower().split('.')[-1]
        mime_type_map = {
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'gif': 'image/gif',
            'webp': 'image/webp',
            'bmp': 'image/bmp',
            'pdf': 'application/pdf'
        }
        return mime_type_map.get(extension, 'image/png')

    def format_file_list(self, documents: List[Dict[str, Any]]) -> str:
        """Format image list for display

        Args:
            documents: List of document info dicts from list_s3_documents()

        Returns:
            Formatted string for display
        """
        if not documents:
            return "**Workspace**: Empty (no images yet)"

        lines = [f"**Workspace** ({len(documents)} image{'s' if len(documents) > 1 else ''}):"]

        for doc in sorted(documents, key=lambda x: x['last_modified'], reverse=True):
            # Parse ISO timestamp
            modified_date = doc['last_modified'].split('T')[0]
            lines.append(f"  - **{doc['filename']}** ({doc['size_kb']}) - Modified: {modified_date}")

        return "\n".join(lines)


class ZipManager(BaseDocumentManager):
    """Document manager for ZIP archive files (.zip)"""

    def __init__(self, user_id: str, session_id: str):
        super().__init__(user_id, session_id, document_type='zip')
        logger.info("ZipManager initialized")

    def format_file_list(self, documents: List[Dict[str, Any]]) -> str:
        if not documents:
            return "**Workspace**: Empty (no zip archives yet)"

        lines = [f"**Workspace** ({len(documents)} archive{'s' if len(documents) > 1 else ''}):"]
        for doc in sorted(documents, key=lambda x: x['last_modified'], reverse=True):
            modified_date = doc['last_modified'].split('T')[0]
            lines.append(f"  - **{doc['filename']}** ({doc['size_kb']}) - Modified: {modified_date}")
        return "\n".join(lines)


# Backward compatibility aliases
WordDocumentManager = WordManager
ExcelDocumentManager = ExcelManager
PowerPointDocumentManager = PowerPointManager
ImageDocumentManager = ImageManager
