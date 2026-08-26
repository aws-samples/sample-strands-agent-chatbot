"""Single canonical publisher for Code Interpreter generated files."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .blob_store import BlobStore, blob_store_from_environment
from .models import SessionFileRef, SessionFileRole, SessionFileState
from .repository import (
    DynamoSessionFileRepository,
    SessionFileRepository,
)

logger = logging.getLogger(__name__)


class PublishError(RuntimeError):
    pass


def _parse_ci_stdout(response: dict) -> str:
    stdout = []
    errors = []
    for event in response.get("stream", []):
        result = event.get("result", {})
        structured = result.get("structuredContent", {})
        if result.get("isError"):
            errors.append(structured.get("stderr", "Code Interpreter failed"))
        value = structured.get("stdout")
        if value:
            stdout.append(value)
    if errors:
        raise PublishError("\n".join(errors))
    return "".join(stdout).strip()


@dataclass
class SessionFilePublisher:
    repository: SessionFileRepository
    blob_store: BlobStore

    @classmethod
    def from_environment(cls) -> "SessionFilePublisher":
        return cls(
            repository=DynamoSessionFileRepository.from_environment(),
            blob_store=blob_store_from_environment(),
        )

    def publish_code_interpreter_file(
        self,
        *,
        code_interpreter: Any,
        source_path: str,
        user_id: str,
        session_id: str,
        filename: str,
        media_type: str,
        artifact_type: str,
        producer_tool: str,
        producer_id: str,
        idempotency_key: str,
    ) -> SessionFileRef:
        session_file = self.repository.reserve(
            user_id=user_id,
            session_id=session_id,
            filename=filename,
            media_type=media_type,
            artifact_type=artifact_type,
            role=SessionFileRole.OUTPUT,
            producer_tool=producer_tool,
            producer_id=producer_id,
            idempotency_key=idempotency_key,
        )
        if session_file.state == SessionFileState.READY:
            return session_file.to_ref()

        try:
            inspection = self._inspect_file(code_interpreter, source_path)
            blob_ref = self.blob_store.allocate(session_file)
            session_file = self.repository.mark_uploading(session_file, blob_ref)
            if session_file.state == SessionFileState.READY:
                return session_file.to_ref()
            target = self.blob_store.create_upload_target(
                blob_ref,
                media_type=media_type,
                checksum_sha256_base64=inspection["checksumSha256Base64"],
            )
            self._upload_file(
                code_interpreter,
                source_path=source_path,
                target=target,
                expected_size=inspection["sizeBytes"],
            )
            metadata = self.blob_store.verify(
                blob_ref,
                expected_size=inspection["sizeBytes"],
                expected_checksum_sha256_base64=inspection["checksumSha256Base64"],
            )
            ready = self.repository.mark_ready(session_file, blob_ref, metadata)
            return ready.to_ref()
        except Exception as error:
            logger.exception(
                "Session file publish failed: user=%s session=%s file=%s",
                user_id,
                session_id,
                filename,
            )
            try:
                self.repository.mark_failed(
                    session_file,
                    code=error.__class__.__name__,
                    message=str(error),
                )
            except Exception:
                logger.exception("Could not mark session file publish as failed")
            if isinstance(error, PublishError):
                raise
            raise PublishError(str(error)) from error

    @staticmethod
    def _inspect_file(code_interpreter: Any, source_path: str) -> dict:
        code = (
            "import base64, hashlib, json, os\n"
            f"_path = {source_path!r}\n"
            "if not os.path.isfile(_path):\n"
            "    raise FileNotFoundError(_path)\n"
            "_hash = hashlib.sha256()\n"
            "_size = 0\n"
            "with open(_path, 'rb') as _file:\n"
            "    while True:\n"
            "        _chunk = _file.read(1024 * 1024)\n"
            "        if not _chunk:\n"
            "            break\n"
            "        _hash.update(_chunk)\n"
            "        _size += len(_chunk)\n"
            "print(json.dumps({\n"
            "    'sizeBytes': _size,\n"
            "    'checksumSha256': _hash.hexdigest(),\n"
            "    'checksumSha256Base64': base64.b64encode(_hash.digest()).decode('ascii'),\n"
            "}))\n"
        )
        response = code_interpreter.invoke(
            "executeCode",
            {"code": code, "language": "python", "clearContext": False},
        )
        stdout = _parse_ci_stdout(response)
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise PublishError("Code Interpreter returned invalid file metadata") from error
        if not isinstance(result.get("sizeBytes"), int) or result["sizeBytes"] < 0:
            raise PublishError("Code Interpreter returned invalid file size")
        return result

    @staticmethod
    def _upload_file(
        code_interpreter: Any,
        *,
        source_path: str,
        target,
        expected_size: int,
    ) -> None:
        code = (
            "import http.client, json, os, urllib.parse\n"
            f"_source = {source_path!r}\n"
            f"_url = {target.url!r}\n"
            f"_method = {target.method!r}\n"
            f"_headers = {target.headers!r}\n"
            f"_expected_size = {expected_size!r}\n"
            "if os.path.getsize(_source) != _expected_size:\n"
            "    raise RuntimeError('Source file changed before publish')\n"
            "_parts = urllib.parse.urlsplit(_url)\n"
            "_request_path = _parts.path + (('?' + _parts.query) if _parts.query else '')\n"
            "_headers = dict(_headers)\n"
            "_headers['Content-Length'] = str(_expected_size)\n"
            "_connection = http.client.HTTPSConnection(_parts.hostname, _parts.port or 443, timeout=120)\n"
            "try:\n"
            "    with open(_source, 'rb') as _body:\n"
            "        _connection.request(_method, _request_path, body=_body, headers=_headers)\n"
            "        _response = _connection.getresponse()\n"
            "        _response_body = _response.read(4096)\n"
            "    if _response.status < 200 or _response.status >= 300:\n"
            "        raise RuntimeError(f'Blob upload failed with HTTP {_response.status}')\n"
            "    print(json.dumps({'status': _response.status, 'sizeBytes': _expected_size}))\n"
            "finally:\n"
            "    _connection.close()\n"
        )
        response = code_interpreter.invoke(
            "executeCode",
            {"code": code, "language": "python", "clearContext": False},
        )
        stdout = _parse_ci_stdout(response)
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise PublishError("Code Interpreter returned invalid upload result") from error
        if result.get("status") not in {200, 201, 204}:
            raise PublishError("Code Interpreter did not confirm blob upload")
