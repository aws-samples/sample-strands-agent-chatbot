"""Durable asynchronous delegation jobs.

Delegation contexts are intentionally ephemeral. This module owns the durable
control-plane record, result artifact, cancellation intent, and mailbox
completion event. The remote A2A task may be retried; callers must treat the job
record and deterministic completion event as the source of truth.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

import boto3
from boto3.dynamodb.conditions import Key

from agent import async_tasks
from agent.factory.session_manager_factory import get_sessions_dir

logger = logging.getLogger(__name__)

EventFactory = Callable[[str], AsyncIterator[dict[str, Any]]]

_TERMINAL_TTL_DAYS = 30
_RESULT_SUMMARY_MAX_CHARS = 8_000
_STALE_HEARTBEAT_SECONDS = 180
_MAX_ATTEMPTS = 3
_MAX_ACTIVE_PER_SESSION = 2
_ALLOWED_PROFILES = frozenset({"analyst", "reviewer"})
_LOCAL_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TERMINAL_EXECUTION_STATES = frozenset(
    {"succeeded", "failed", "cancelled", "timed_out"}
)


class DelegationConflictError(RuntimeError):
    """Raised when an idempotency key is reused for a different request."""


class DelegationNotFoundError(KeyError):
    """Raised when the requested delegation job does not exist."""


@dataclass(frozen=True)
class DelegationReceipt:
    job_id: str
    status: str
    profile: str

    def as_dict(self) -> dict[str, str]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "profile": self.profile,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _terminal_ttl() -> int:
    return int(
        (datetime.now(timezone.utc) + timedelta(days=_TERMINAL_TTL_DAYS)).timestamp()
    )


def _session_key(user_id: str, session_id: str) -> str:
    return f"USER#{user_id}#SESSION#{session_id}"


def _record_key(job_id: str) -> str:
    return f"JOB#{job_id}"


def _validated_local_component(value: str, name: str) -> str:
    if not _LOCAL_COMPONENT_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid local delegation {name}")
    return value


def _local_storage_key(value: str, name: str) -> str:
    component = _validated_local_component(value, name)
    return hashlib.sha256(component.encode("utf-8")).hexdigest()


def _local_dir(session_id: str) -> Path:
    sessions_root = get_sessions_dir().resolve()
    storage_root = (sessions_root / "delegation_jobs").resolve()
    if not storage_root.is_relative_to(sessions_root):
        raise ValueError("Local delegation directory escapes the sessions root")
    storage_root.mkdir(parents=True, exist_ok=True)

    session_key = _local_storage_key(session_id, "session ID")
    candidate = storage_root / session_key
    if candidate.is_symlink():
        raise ValueError("Local delegation session directory cannot be a symlink")
    path = candidate.resolve()
    if path.parent != storage_root:
        raise ValueError("Local delegation directory escapes the storage root")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _local_job_path(
    session_id: str,
    job_id: str,
    *,
    result: bool = False,
) -> Path:
    jobs_dir = _local_dir(session_id)
    job_key = _local_storage_key(job_id, "job ID")
    suffix = ".result.json" if result else ".json"
    path = (jobs_dir / f"{job_key}{suffix}").resolve()
    if path.parent != jobs_dir:
        raise ValueError("Local delegation file escapes the job directory")
    return path


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _is_cloud() -> bool:
    return bool(os.environ.get("SESSION_ORCHESTRATION_TABLE"))


def _table():
    return boto3.resource(
        "dynamodb",
        region_name=os.environ.get("AWS_REGION", "us-west-2"),
    ).Table(os.environ["SESSION_ORCHESTRATION_TABLE"])


def _canonical_request(request: dict[str, Any]) -> str:
    return json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_wire_value(value: Any) -> Any:
    """Convert DynamoDB-native values into lossless JSON-compatible values."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {
            str(key): _json_wire_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_wire_value(item) for item in value]
    return value


def request_hash(request: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_request(request).encode("utf-8")).hexdigest()


def job_id_for(idempotency_key: str) -> str:
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]


def _save(record: dict[str, Any]) -> None:
    if _is_cloud():
        _table().put_item(Item=record)
        return
    path = _local_job_path(record["sessionId"], record["jobId"])
    _atomic_write(path, json.dumps(record, ensure_ascii=False, indent=2))


def _terminalize(record: dict[str, Any], status: str, **updates: Any) -> None:
    terminal_updates = dict(
        executionStatus=status,
        workStatus="terminal",
        completedAt=_now(),
        heartbeatAt=_now(),
        updatedAt=_now(),
        ttl=_terminal_ttl(),
        **updates,
    )
    if _is_cloud() and record.get("executionToken"):
        _update_owned(record, terminal_updates)
        return
    record.update(terminal_updates)
    _save(record)


def _update_owned(
    record: dict[str, Any],
    updates: dict[str, Any],
) -> bool:
    """Update an execution only while its fencing token still owns the job."""
    if _is_cloud():
        names: dict[str, str] = {}
        values: dict[str, Any] = {
            ":token": record["executionToken"],
            ":running": "running",
        }
        assignments = []
        for index, (key, value) in enumerate(updates.items()):
            name_token = f"#field{index}"
            value_token = f":value{index}"
            names[name_token] = key
            values[value_token] = value
            assignments.append(f"{name_token} = {value_token}")
        condition = (
            "executionToken = :token "
            "AND desiredState = :running AND workStatus = :running"
        )
        try:
            response = _table().update_item(
                Key={
                    "sessionKey": record["sessionKey"],
                    "recordKey": record["recordKey"],
                },
                UpdateExpression="SET " + ", ".join(assignments),
                ConditionExpression=condition,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )
        except Exception as error:
            code = getattr(error, "response", {}).get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                return False
            raise
        record.update(response.get("Attributes") or updates)
        return True

    latest = get_job(record["userId"], record["sessionId"], record["jobId"])
    if (
        latest is None
        or latest.get("executionToken") != record.get("executionToken")
        or (
            latest.get("desiredState") != "running"
            or latest.get("workStatus") != "running"
        )
    ):
        return False
    latest.update(updates)
    _save(latest)
    record.update(latest)
    return True


def _create(record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Create a job or return the existing idempotent record."""
    if _is_cloud():
        try:
            _table().put_item(
                Item=record,
                ConditionExpression=(
                    "attribute_not_exists(sessionKey) "
                    "AND attribute_not_exists(recordKey)"
                ),
            )
            return record, True
        except Exception as error:
            code = getattr(error, "response", {}).get("Error", {}).get("Code")
            if code != "ConditionalCheckFailedException":
                raise
            existing = get_job(
                record["userId"],
                record["sessionId"],
                record["jobId"],
            )
    else:
        path = _local_job_path(record["sessionId"], record["jobId"])
        if not path.exists():
            _atomic_write(path, json.dumps(record, ensure_ascii=False, indent=2))
            return record, True
        existing = json.loads(path.read_text(encoding="utf-8"))

    if existing is None:
        raise RuntimeError("Delegation record disappeared during idempotent create")
    if existing.get("requestHash") != record.get("requestHash"):
        raise DelegationConflictError(
            "The delegation idempotency key was reused with a different request"
        )
    return existing, False


def get_job(
    user_id: str,
    session_id: str,
    job_id: str,
) -> Optional[dict[str, Any]]:
    if _is_cloud():
        response = _table().get_item(
            Key={
                "sessionKey": _session_key(user_id, session_id),
                "recordKey": _record_key(job_id),
            },
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item and item.get("recordType") == "DELEGATION_JOB":
            return item
        return None

    path = _local_job_path(session_id, job_id)
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if (
        record.get("userId") != user_id
        or record.get("sessionId") != session_id
        or record.get("recordType") != "DELEGATION_JOB"
    ):
        return None
    return record


def list_jobs(user_id: str, session_id: str) -> list[dict[str, Any]]:
    if _is_cloud():
        response = _table().query(
            KeyConditionExpression=(
                Key("sessionKey").eq(_session_key(user_id, session_id))
                & Key("recordKey").begins_with("JOB#")
            ),
            ConsistentRead=True,
        )
        return [
            item
            for item in response.get("Items", [])
            if item.get("recordType") == "DELEGATION_JOB"
        ]

    records = []
    jobs_dir = _local_dir(session_id)
    for candidate in jobs_dir.glob("*.json"):
        if candidate.is_symlink():
            continue
        path = candidate.resolve()
        if path.parent != jobs_dir:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            record.get("userId") == user_id
            and record.get("recordType") == "DELEGATION_JOB"
        ):
            records.append(record)
    return records


def _save_result(record: dict[str, Any], result: dict[str, Any]) -> dict[str, str]:
    body = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
    if _is_cloud():
        from workspace.config import get_workspace_bucket

        bucket = get_workspace_bucket()
        key = (
            f"delegation-artifacts/{record['userId']}/{record['sessionId']}/"
            f"{record['jobId']}.json"
        )
        boto3.client(
            "s3",
            region_name=os.environ.get("AWS_REGION", "us-west-2"),
        ).put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json; charset=utf-8",
        )
        return {"resultBucket": bucket, "resultS3Key": key}

    path = _local_job_path(
        record["sessionId"],
        record["jobId"],
        result=True,
    )
    _atomic_write(path, body.decode("utf-8"))
    return {"resultPath": str(path)}


def load_result(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("resultBucket") and record.get("resultS3Key"):
        response = boto3.client(
            "s3",
            region_name=os.environ.get("AWS_REGION", "us-west-2"),
        ).get_object(
            Bucket=record["resultBucket"],
            Key=record["resultS3Key"],
        )
        return json.loads(response["Body"].read())
    if not record.get("resultPath"):
        raise RuntimeError(f"Delegation result is missing for {record['jobId']}")
    path = _local_job_path(
        record["sessionId"],
        record["jobId"],
        result=True,
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_result(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        parsed = {"summary": text}
    if not isinstance(parsed, dict):
        parsed = {"summary": str(parsed)}

    summary = str(parsed.get("summary") or "").strip()
    if not summary:
        summary = "Delegated task completed."
    if len(summary) > _RESULT_SUMMARY_MAX_CHARS:
        summary = summary[:_RESULT_SUMMARY_MAX_CHARS] + "\n\n[Summary truncated]"

    def _list(name: str) -> list[Any]:
        value = parsed.get(name, [])
        return value if isinstance(value, list) else [value]

    return {
        "summary": summary,
        "findings": _list("findings"),
        "artifacts": _list("artifacts"),
        "openQuestions": _list("openQuestions"),
        "scopeExceptions": _list("scopeExceptions"),
    }


def _completion_event_id(job_id: str) -> str:
    return f"delegation-result:{job_id}"


def _publish_completion(record: dict[str, Any], result: dict[str, Any]) -> str:
    from agent.mailbox import MailboxEvent, get_mailbox_repository

    event_id = _completion_event_id(record["jobId"])
    payload_ref = None
    if record.get("resultBucket") and record.get("resultS3Key"):
        payload_ref = {
            "bucket": record["resultBucket"],
            "key": record["resultS3Key"],
        }
    elif record.get("resultPath"):
        payload_ref = {"path": record["resultPath"]}

    event = MailboxEvent.create(
        event_id=event_id,
        event_type="async_result.ready",
        session_id=record["sessionId"],
        user_id=record["userId"],
        source_type="delegation_job",
        source_id=record["jobId"],
        correlation={
            "jobId": record["jobId"],
            "profile": record["profile"],
            "parentRunId": record.get("parentRunId", ""),
            "parentToolUseId": record.get("parentToolUseId", ""),
        },
        payload={
            "resultType": "delegation",
            "profile": record["profile"],
            "summary": result["summary"],
            "artifacts": result["artifacts"],
        },
        payload_ref=payload_ref,
        conversation_epoch=int(record.get("conversationEpoch", 0)),
    )
    get_mailbox_repository().enqueue(event)
    return event_id


def _cancel_requested(record: dict[str, Any]) -> bool:
    latest = get_job(record["userId"], record["sessionId"], record["jobId"])
    return bool(latest and latest.get("desiredState") == "cancelled")


def _claim_job(
    user_id: str,
    session_id: str,
    job_id: str,
) -> Optional[dict[str, Any]]:
    """Claim queued work or take over one stale execution."""
    current = get_job(user_id, session_id, job_id)
    if current is None:
        raise DelegationNotFoundError(job_id)
    if (
        current.get("desiredState") == "cancelled"
        or current.get("executionStatus") in _TERMINAL_EXECUTION_STATES
    ):
        if current.get("executionStatus") not in _TERMINAL_EXECUTION_STATES:
            _terminalize(current, "cancelled")
        return None

    now = _now()
    stale_before = (
        datetime.now(timezone.utc)
        - timedelta(seconds=_STALE_HEARTBEAT_SECONDS)
    ).isoformat()
    execution_token = uuid.uuid4().hex
    if _is_cloud():
        try:
            response = _table().update_item(
                Key={
                    "sessionKey": _session_key(user_id, session_id),
                    "recordKey": _record_key(job_id),
                },
                UpdateExpression=(
                    "SET executionStatus = :running, workStatus = :running, "
                    "heartbeatAt = :now, updatedAt = :now, "
                    "startedAt = if_not_exists(startedAt, :now), "
                    "attempts = if_not_exists(attempts, :zero) + :one, "
                    "executionToken = :token"
                ),
                ConditionExpression=(
                    "recordType = :recordType AND desiredState = :desired "
                    "AND attempts < :maxAttempts AND ("
                    "workStatus = :queued OR "
                    "(workStatus = :running AND heartbeatAt < :stale))"
                ),
                ExpressionAttributeValues={
                    ":running": "running",
                    ":queued": "queued",
                    ":now": now,
                    ":stale": stale_before,
                    ":zero": 0,
                    ":one": 1,
                    ":maxAttempts": _MAX_ATTEMPTS,
                    ":token": execution_token,
                    ":recordType": "DELEGATION_JOB",
                    ":desired": "running",
                },
                ReturnValues="ALL_NEW",
            )
            return response.get("Attributes")
        except Exception as error:
            code = getattr(error, "response", {}).get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                return None
            raise

    heartbeat = str(current.get("heartbeatAt") or "")
    if current.get("workStatus", "queued") == "running" and heartbeat >= stale_before:
        return None
    attempts = int(current.get("attempts", 0))
    if attempts >= _MAX_ATTEMPTS:
        _terminalize(
            current,
            "failed",
            deliveryStatus="none",
            error="Delegation retry limit exceeded",
        )
        return None
    current.update(
        executionStatus="running",
        workStatus="running",
        heartbeatAt=now,
        updatedAt=now,
        startedAt=current.get("startedAt") or now,
        attempts=attempts + 1,
        executionToken=execution_token,
    )
    _save(current)
    return current


def _heartbeat(record: dict[str, Any]) -> bool:
    """Refresh one owned execution and observe cancellation or takeover."""
    heartbeat_at = _now()
    return _update_owned(
        record,
        {"heartbeatAt": heartbeat_at, "updatedAt": heartbeat_at},
    )


def _event_factory(record: dict[str, Any]) -> EventFactory:
    from a2a_tools import send_a2a_message

    request = _json_wire_value(record["request"])
    attempt = int(record.get("attempts", 1))
    delegation_session_id = f"delegation-{record['jobId']}-{attempt}"
    metadata = _json_wire_value({
        "profile": record["profile"],
        "job_id": record["jobId"],
        "session_id": record["sessionId"],
        "user_id": record["userId"],
        "model_id": record.get("modelId", ""),
        "workspace_paths": request.get("workspacePaths", []),
        "output_path": f"outputs/delegations/{record['jobId']}",
        "max_seconds": int(
            request.get("budget", {}).get("maxSeconds", 600)
        ),
    })

    def factory(_job_id: str) -> AsyncIterator[dict[str, Any]]:
        return send_a2a_message(
            "agentcore_general-subagent",
            json.dumps(request, ensure_ascii=False),
            delegation_session_id,
            os.environ.get("AWS_REGION", "us-west-2"),
            metadata=metadata,
            auth_token=record.get("_authToken"),
        )

    return factory


async def _run(
    record: dict[str, Any],
    event_factory: EventFactory,
) -> None:
    task_id = async_tasks.begin(
        "delegation",
        {"job_id": record["jobId"], "profile": record["profile"]},
    )
    producer: Optional[asyncio.Task] = None
    try:
        if _cancel_requested(record):
            _terminalize(record, "cancelled")
            return

        final_text = ""
        remote_task_id = ""
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        async def produce() -> None:
            try:
                async for streamed_event in event_factory(record["jobId"]):
                    await queue.put(("event", streamed_event))
            except BaseException as error:
                await queue.put(("error", error))
            finally:
                await queue.put(("done", None))

        producer = asyncio.create_task(produce())
        deadline = (
            asyncio.get_running_loop().time()
            + int(record["request"].get("budget", {}).get("maxSeconds", 600))
        )
        while True:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Delegation execution timed out")
            try:
                kind, value = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                if not _heartbeat(record):
                    raise asyncio.CancelledError
                continue
            if kind == "done":
                break
            if kind == "error":
                raise value
            event = value
            if not _heartbeat(record):
                raise asyncio.CancelledError
            if not isinstance(event, dict):
                continue
            if event.get("taskId"):
                remote_task_id = str(event["taskId"])
                updated_at = _now()
                if not _update_owned(
                    record,
                    {
                        "remoteTaskId": remote_task_id,
                        "heartbeatAt": updated_at,
                        "updatedAt": updated_at,
                    },
                ):
                    raise asyncio.CancelledError
            if event.get("type") in {"delegation_step", "research_step", "code_step"}:
                progress = {
                    "content": str(event.get("content") or "")[:1_000],
                    "stepNumber": int(event.get("stepNumber") or 0),
                }
                updated_at = _now()
                updates = {
                    "progress": progress,
                    "heartbeatAt": updated_at,
                    "updatedAt": updated_at,
                }
                if remote_task_id:
                    updates["remoteTaskId"] = remote_task_id
                if not _update_owned(record, updates):
                    raise asyncio.CancelledError
            status = event.get("status")
            if status in {"success", "error"}:
                content = event.get("content") or []
                text = (
                    content[0].get("text", "")
                    if content and isinstance(content[0], dict)
                    else ""
                )
                if status == "error":
                    raise RuntimeError(text or "Delegated agent failed")
                final_text = text

        if not final_text:
            raise RuntimeError("Delegated agent returned an empty result")
        result = _normalize_result(final_text)
        result_location = _save_result(record, result)
        success_updates = dict(
            **result_location,
            executionStatus="succeeded",
            workStatus="terminal",
            deliveryStatus="pending",
            completedAt=_now(),
            heartbeatAt=_now(),
            updatedAt=_now(),
            ttl=_terminal_ttl(),
            resultSummary=result["summary"],
            artifacts=result["artifacts"],
        )
        if not _update_owned(record, success_updates):
            raise asyncio.CancelledError

        event_id = _publish_completion(record, result)
        record.update(
            deliveryStatus="published",
            mailboxEventId=event_id,
            updatedAt=_now(),
        )
        _save(record)
        try:
            from agent.mailbox_runtime import notify_session_mailbox

            await notify_session_mailbox(
                record["userId"],
                record["sessionId"],
                event_ids=[event_id],
            )
        except Exception:
            logger.exception(
                "[DelegationJob] Completion wake failed for %s; "
                "durable mailbox delivery will retry",
                record["jobId"],
            )
    except asyncio.CancelledError:
        _terminalize(record, "cancelled")
    except TimeoutError as error:
        _terminalize(
            record,
            "timed_out",
            deliveryStatus="none",
            error=str(error),
        )
    except Exception as error:
        if int(record.get("attempts", 1)) < _MAX_ATTEMPTS:
            updated_at = _now()
            if _update_owned(
                record,
                {
                    "executionStatus": "queued",
                    "workStatus": "queued",
                    "heartbeatAt": updated_at,
                    "updatedAt": updated_at,
                    "lastError": str(error),
                },
            ):
                logger.warning(
                    "[DelegationJob] Job %s queued for retry after attempt %s: %s",
                    record["jobId"],
                    record.get("attempts"),
                    error,
                )
            else:
                logger.info(
                    "[DelegationJob] Job %s lost ownership while failing",
                    record["jobId"],
                )
        else:
            _terminalize(
                record,
                "failed",
                deliveryStatus="none",
                error=str(error),
            )
            logger.exception("[DelegationJob] Job %s failed", record["jobId"])
    finally:
        if producer and not producer.done():
            producer.cancel()
            try:
                await producer
            except BaseException:
                pass
        async_tasks.end(task_id)


def start_job(
    *,
    user_id: str,
    session_id: str,
    idempotency_key: str,
    profile: str,
    request: dict[str, Any],
    parent_run_id: str = "",
    parent_tool_use_id: str = "",
    model_id: str = "",
) -> DelegationReceipt:
    if profile not in _ALLOWED_PROFILES:
        raise ValueError(f"Unsupported delegation profile: {profile}")

    job_id = job_id_for(idempotency_key)
    existing = get_job(user_id, session_id, job_id)
    expected_hash = request_hash(request)
    if existing is not None:
        if existing.get("requestHash") != expected_hash:
            raise DelegationConflictError(
                "The delegation idempotency key was reused with a different request"
            )
        return DelegationReceipt(
            job_id=job_id,
            status=existing["executionStatus"],
            profile=existing["profile"],
        )
    active_count = sum(
        job.get("executionStatus") in {"queued", "running"}
        for job in list_jobs(user_id, session_id)
    )
    if active_count >= _MAX_ACTIVE_PER_SESSION:
        raise ValueError(
            f"A session may run at most {_MAX_ACTIVE_PER_SESSION} delegations"
        )

    created_at = _now()
    conversation_epoch = 0
    if os.environ.get("SESSION_MAILBOX_WRITE_ENABLED", "").lower() == "true":
        from agent.mailbox import get_mailbox_repository

        conversation_epoch = get_mailbox_repository().get_conversation_epoch(
            user_id,
            session_id,
        )

    record = {
        "sessionKey": _session_key(user_id, session_id),
        "recordKey": _record_key(job_id),
        "recordType": "DELEGATION_JOB",
        "jobId": job_id,
        "userId": user_id,
        "sessionId": session_id,
        "profile": profile,
        "request": request,
        "requestHash": expected_hash,
        "idempotencyKey": idempotency_key,
        "parentRunId": parent_run_id,
        "parentToolUseId": parent_tool_use_id,
        "modelId": model_id,
        "conversationEpoch": conversation_epoch,
        "executionStatus": "queued",
        "workStatus": "queued",
        "deliveryStatus": "none",
        "desiredState": "running",
        "attempts": 0,
        "heartbeatAt": created_at,
        "createdAt": created_at,
        "updatedAt": created_at,
    }
    existing, created = _create(record)
    if created and not _is_cloud():
        thread = threading.Thread(
            target=lambda: start_job_execution(
                user_id,
                session_id,
                job_id,
            ),
            name=f"delegation-{job_id[:8]}",
            daemon=True,
        )
        thread.start()
    return DelegationReceipt(
        job_id=job_id,
        status=existing["executionStatus"],
        profile=existing["profile"],
    )


def start_job_execution(
    user_id: str,
    session_id: str,
    job_id: str,
    *,
    auth_token: str = "",
) -> dict[str, Any]:
    """Conditionally claim and start one delegation in the background."""
    record = _claim_job(user_id, session_id, job_id)
    if record is None:
        existing = get_job(user_id, session_id, job_id)
        return {
            "status": "ignored",
            "executionStatus": (
                existing.get("executionStatus") if existing else "not_found"
            ),
        }
    record["_authToken"] = auth_token or None
    thread = threading.Thread(
        target=lambda: asyncio.run(_run(record, _event_factory(record))),
        name=f"delegation-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return {
        "status": "accepted",
        "executionStatus": "running",
        "jobId": job_id,
    }


def cancel_job(user_id: str, session_id: str, job_id: str) -> dict[str, Any]:
    record = get_job(user_id, session_id, job_id)
    if record is None:
        raise DelegationNotFoundError(job_id)
    if record.get("executionStatus") in _TERMINAL_EXECUTION_STATES:
        return record
    if _is_cloud():
        response = _table().update_item(
            Key={
                "sessionKey": _session_key(user_id, session_id),
                "recordKey": _record_key(job_id),
            },
            UpdateExpression=(
                "SET desiredState = :cancelled, executionStatus = :cancelled, "
                "workStatus = :terminal, updatedAt = :updated, "
                "completedAt = :updated, #ttl = :ttl"
            ),
            ConditionExpression=(
                "recordType = :recordType AND "
                "(executionStatus = :queued OR executionStatus = :running)"
            ),
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={
                ":cancelled": "cancelled",
                ":terminal": "terminal",
                ":updated": _now(),
                ":ttl": _terminal_ttl(),
                ":recordType": "DELEGATION_JOB",
                ":queued": "queued",
                ":running": "running",
            },
            ReturnValues="ALL_NEW",
        )
        return response["Attributes"]
    record.update(
        desiredState="cancelled",
        executionStatus="cancelled",
        workStatus="terminal",
        updatedAt=_now(),
        completedAt=_now(),
        ttl=_terminal_ttl(),
    )
    _save(record)
    return record


def mark_delivered(record: dict[str, Any]) -> None:
    record.update(
        deliveryStatus="delivered",
        deliveredAt=_now(),
        updatedAt=_now(),
        ttl=_terminal_ttl(),
    )
    _save(record)


def public_job(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record.get(key)
        for key in (
            "jobId",
            "profile",
            "executionStatus",
            "deliveryStatus",
            "progress",
            "resultSummary",
            "artifacts",
            "error",
            "createdAt",
            "startedAt",
            "completedAt",
        )
        if record.get(key) is not None
    }
