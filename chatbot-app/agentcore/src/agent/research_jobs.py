"""Durable background jobs for non-blocking research.

Research runs in its own thread and event loop because skill tools may execute
inside a ThreadPoolExecutor. Job metadata is persisted before the worker starts,
and the report is persisted before completion is announced or delivered.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional
from urllib.parse import quote
from uuid import uuid4

import boto3

from agent import async_tasks
from agent.factory.session_manager_factory import get_sessions_dir

logger = logging.getLogger(__name__)

EventFactory = Callable[[], AsyncIterator[Dict[str, Any]]]
DeliveryHandler = Callable[[Dict[str, Any], Dict[str, Any]], Awaitable[None]]

_delivery_loop: Optional[asyncio.AbstractEventLoop] = None
_delivery_handler: Optional[DeliveryHandler] = None
_delivery_lock = threading.Lock()


class CompletionPublishStatus(str, Enum):
    DISABLED = "disabled"
    DURABLE = "durable"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class CompletionPublishResult:
    status: CompletionPublishStatus
    event_id: Optional[str] = None
    error: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", value)
    return cleaned or "unknown"


def _job_sk(session_id: str, job_id: str) -> str:
    return f"RESEARCH_JOB#{session_id}#{job_id}"


def _orchestration_session_key(user_id: str, session_id: str) -> str:
    return f"USER#{user_id}#SESSION#{session_id}"


def _orchestration_job_key(job_id: str) -> str:
    return f"JOB#{job_id}"


def _local_job_dir(session_id: str) -> Path:
    path = get_sessions_dir() / f"session_{_safe_component(session_id)}" / "research_jobs"
    path.mkdir(parents=True, exist_ok=True)
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


def _is_cloud_storage() -> bool:
    return bool(os.environ.get("DYNAMODB_USERS_TABLE"))


def _legacy_job_reads_enabled() -> bool:
    return (
        not os.environ.get("SESSION_ORCHESTRATION_TABLE")
        or os.environ.get(
            "RESEARCH_JOB_LEGACY_READ_ENABLED",
            "",
        ).lower() == "true"
    )


def _save_job(record: Dict[str, Any]) -> None:
    if _is_cloud_storage():
        region = os.environ.get("AWS_REGION", "us-west-2")
        orchestration_table = os.environ.get("SESSION_ORCHESTRATION_TABLE")
        if orchestration_table:
            table_name = orchestration_table
            item = {
                **record,
                "sessionKey": _orchestration_session_key(
                    record["userId"],
                    record["sessionId"],
                ),
                "recordKey": _orchestration_job_key(record["jobId"]),
                "recordType": "JOB",
            }
        else:
            table_name = os.environ["DYNAMODB_USERS_TABLE"]
            item = {
                **record,
                "userId": record["userId"],
                "sk": _job_sk(record["sessionId"], record["jobId"]),
                "recordType": "RESEARCH_JOB",
            }
        boto3.resource("dynamodb", region_name=region).Table(table_name).put_item(Item=item)
        return

    path = _local_job_dir(record["sessionId"]) / f"{_safe_component(record['jobId'])}.json"
    _atomic_write(path, json.dumps(record, ensure_ascii=False, indent=2))


def _get_job(
    user_id: str,
    session_id: str,
    job_id: str,
) -> Optional[Dict[str, Any]]:
    if _is_cloud_storage():
        region = os.environ.get("AWS_REGION", "us-west-2")
        dynamodb = boto3.resource("dynamodb", region_name=region)
        orchestration_table = os.environ.get("SESSION_ORCHESTRATION_TABLE")
        if orchestration_table:
            response = dynamodb.Table(orchestration_table).get_item(
                Key={
                    "sessionKey": _orchestration_session_key(user_id, session_id),
                    "recordKey": _orchestration_job_key(job_id),
                },
                ConsistentRead=True,
            )
            if response.get("Item"):
                return response["Item"]

        if not _legacy_job_reads_enabled():
            return None
        response = dynamodb.Table(os.environ["DYNAMODB_USERS_TABLE"]).get_item(
            Key={
                "userId": user_id,
                "sk": _job_sk(session_id, job_id),
            },
            ConsistentRead=True,
        )
        return response.get("Item")

    path = _local_job_dir(session_id) / f"{_safe_component(job_id)}.json"
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("[ResearchJob] Ignoring unreadable job file %s", path)
        return None
    if record.get("userId") != user_id or record.get("sessionId") != session_id:
        return None
    return record


def _save_report(record: Dict[str, Any], report: str) -> Dict[str, str]:
    if _is_cloud_storage():
        from workspace.config import get_workspace_bucket

        bucket = get_workspace_bucket()
        key = (
            "research-artifacts/"
            f"{quote(record['userId'], safe='')}/"
            f"{quote(record['sessionId'], safe='')}/"
            f"{record['jobId']}.md"
        )
        region = os.environ.get("AWS_REGION", "us-west-2")
        boto3.client("s3", region_name=region).put_object(
            Bucket=bucket,
            Key=key,
            Body=report.encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
        )
        return {"artifactBucket": bucket, "artifactS3Key": key}

    path = _local_job_dir(record["sessionId"]) / f"{_safe_component(record['jobId'])}.md"
    _atomic_write(path, report)
    return {"artifactPath": str(path)}


def _load_report(record: Dict[str, Any]) -> str:
    if record.get("artifactBucket") and record.get("artifactS3Key"):
        region = os.environ.get("AWS_REGION", "us-west-2")
        response = boto3.client("s3", region_name=region).get_object(
            Bucket=record["artifactBucket"],
            Key=record["artifactS3Key"],
        )
        return response["Body"].read().decode("utf-8")

    path = _local_job_dir(record["sessionId"]) / f"{_safe_component(record['jobId'])}.md"
    return path.read_text(encoding="utf-8")


def _list_jobs(user_id: str, session_id: str) -> list[Dict[str, Any]]:
    if _is_cloud_storage():
        region = os.environ.get("AWS_REGION", "us-west-2")
        dynamodb = boto3.resource("dynamodb", region_name=region)
        jobs_by_id: Dict[str, Dict[str, Any]] = {}

        orchestration_table = os.environ.get("SESSION_ORCHESTRATION_TABLE")
        if orchestration_table:
            table = dynamodb.Table(orchestration_table)
            start_key = None
            while True:
                query_kwargs = {
                    "KeyConditionExpression": (
                        "sessionKey = :session_key "
                        "AND begins_with(recordKey, :prefix)"
                    ),
                    "ExpressionAttributeValues": {
                        ":session_key": _orchestration_session_key(
                            user_id,
                            session_id,
                        ),
                        ":prefix": "JOB#",
                    },
                }
                if start_key:
                    query_kwargs["ExclusiveStartKey"] = start_key
                response = table.query(**query_kwargs)
                for item in response.get("Items", []):
                    if item.get("jobId"):
                        jobs_by_id[item["jobId"]] = item
                start_key = response.get("LastEvaluatedKey")
                if not start_key:
                    break

        if not _legacy_job_reads_enabled():
            return list(jobs_by_id.values())

        # Explicit migration path. Disable after legacy rows have aged out.
        legacy_table = dynamodb.Table(os.environ["DYNAMODB_USERS_TABLE"])
        start_key = None
        while True:
            query_kwargs = {
                "KeyConditionExpression": "userId = :user_id AND begins_with(sk, :prefix)",
                "ExpressionAttributeValues": {
                    ":user_id": user_id,
                    ":prefix": f"RESEARCH_JOB#{session_id}#",
                },
            }
            if start_key:
                query_kwargs["ExclusiveStartKey"] = start_key
            response = legacy_table.query(**query_kwargs)
            for item in response.get("Items", []):
                if item.get("jobId") and item["jobId"] not in jobs_by_id:
                    jobs_by_id[item["jobId"]] = item
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                return list(jobs_by_id.values())

    jobs = []
    for path in _local_job_dir(session_id).glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("userId") == user_id and record.get("sessionId") == session_id:
                jobs.append(record)
        except (OSError, ValueError):
            logger.warning("[ResearchJob] Ignoring unreadable job file %s", path)
    return jobs


def _build_artifact(record: Dict[str, Any], report: str) -> Dict[str, Any]:
    title_match = re.search(r"^#\s+(.+)$", report, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else "Research Results"
    timestamp = record.get("completedAt") or _now()
    return {
        "id": record["artifactId"],
        "type": "research",
        "title": title,
        "content": report,
        "tool_name": "research_agent",
        "metadata": {
            "word_count": len(report.split()),
            "description": f"Research report: {title}",
            "job_id": record["jobId"],
        },
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _artifact_payload_ref(record: Dict[str, Any]) -> Optional[Dict[str, str]]:
    if record.get("artifactBucket") and record.get("artifactS3Key"):
        return {
            "bucket": record["artifactBucket"],
            "key": record["artifactS3Key"],
        }
    if record.get("artifactPath"):
        return {"path": record["artifactPath"]}
    return None


def _build_artifact_reference(
    record: Dict[str, Any],
    artifact: Dict[str, Any],
) -> Dict[str, Any]:
    reference = {
        key: value
        for key, value in artifact.items()
        if key != "content"
    }
    payload_ref = _artifact_payload_ref(record)
    if payload_ref:
        reference["content_ref"] = payload_ref
    return reference


def _mailbox_write_enabled() -> bool:
    return os.environ.get("SESSION_MAILBOX_WRITE_ENABLED", "").lower() == "true"


def _completion_event_id(record: Dict[str, Any]) -> str:
    return f"research-result:{record['jobId']}"


def _enqueue_completion_event(record: Dict[str, Any]) -> Optional[str]:
    """Publish the deterministic completion event when mailbox writes are enabled."""
    if not _mailbox_write_enabled():
        return None

    from agent.mailbox import MailboxEvent, get_mailbox_repository

    event_id = _completion_event_id(record)
    payload_ref = _artifact_payload_ref(record)

    event = MailboxEvent.create(
        event_id=event_id,
        event_type="async_result.ready",
        session_id=record["sessionId"],
        user_id=record["userId"],
        source_type="research_job",
        source_id=record["jobId"],
        correlation={
            "jobId": record["jobId"],
            "artifactId": record["artifactId"],
        },
        payload={
            "resultType": "research",
            "artifact": record.get("artifact", {}),
        },
        payload_ref=payload_ref,
        conversation_epoch=int(record.get("conversationEpoch", 0)),
    )
    inserted = get_mailbox_repository().enqueue(event)
    logger.info(
        "[ResearchJob] Mailbox completion %s (%s)",
        event_id,
        "inserted" if inserted else "duplicate",
    )
    return event_id


def _completion_event_exists(record: Dict[str, Any]) -> bool:
    event_id = record.get("mailboxEventId") or _completion_event_id(record)
    from agent.mailbox import get_mailbox_repository

    return get_mailbox_repository().get_event(
        record["userId"],
        record["sessionId"],
        event_id,
    ) is not None


def _publish_completion(record: Dict[str, Any]) -> CompletionPublishResult:
    try:
        event_id = _enqueue_completion_event(record)
        if event_id is None:
            return CompletionPublishResult(CompletionPublishStatus.DISABLED)
        return CompletionPublishResult(
            CompletionPublishStatus.DURABLE,
            event_id=event_id,
        )
    except Exception as exc:
        from agent.mailbox import SessionDeletedError, SessionSupersededError

        if isinstance(exc, (SessionDeletedError, SessionSupersededError)):
            return CompletionPublishResult(
                CompletionPublishStatus.CANCELLED,
                error=str(exc),
            )
        try:
            event_exists = _completion_event_exists(record)
        except Exception:
            logger.exception(
                "[ResearchJob] Failed to reconcile mailbox write for %s",
                record["jobId"],
            )
            return CompletionPublishResult(
                CompletionPublishStatus.FAILED,
                error=str(exc),
            )
        if event_exists:
            logger.warning(
                "[ResearchJob] Recovered ambiguous mailbox write for %s",
                record["jobId"],
            )
            return CompletionPublishResult(
                CompletionPublishStatus.DURABLE,
                event_id=record.get("mailboxEventId")
                or _completion_event_id(record),
            )
        logger.exception(
            "[ResearchJob] Mailbox write failed for %s",
            record["jobId"],
        )
        return CompletionPublishResult(
            CompletionPublishStatus.FAILED,
            error=str(exc),
        )


def load_pending_results(user_id: str, session_id: str) -> list[Dict[str, Any]]:
    """Load reports that are durable but were not delivered to the agent."""
    pending = []
    try:
        for record in _list_jobs(user_id, session_id):
            status = record.get("status")
            if status not in ("completed", "delivering"):
                continue
            try:
                updated_at = datetime.fromisoformat(record["updatedAt"])
                stale = (datetime.now(timezone.utc) - updated_at).total_seconds() >= 10
            except (KeyError, TypeError, ValueError):
                stale = True
            # A live worker briefly writes completed before switching to
            # delivering. Only recover immediately after an explicit delivery
            # failure; otherwise wait for the lease to become stale.
            if not record.get("deliveryError") and not stale:
                continue
            report = _load_report(record)
            pending.append({
                "record": record,
                "artifact": _build_artifact(record, report),
            })
    except Exception:
        logger.exception(
            "[ResearchJob] Failed to load pending results for %s",
            session_id,
        )
    return pending


def recover_pending_mailbox_events(user_id: str, session_id: str) -> list[str]:
    """Recreate deterministic mailbox events for legacy or interrupted jobs."""
    recovered = []
    for item in load_pending_results(user_id, session_id):
        record = item["record"]
        from agent.mailbox import PROCESSED, get_mailbox_repository

        try:
            existing = get_mailbox_repository().get_event(
                user_id,
                session_id,
                record.get("mailboxEventId") or _completion_event_id(record),
            )
        except Exception:
            logger.exception(
                "[ResearchJob] Failed to inspect completion event for %s",
                record["jobId"],
            )
            existing = None
        if existing and existing.status == PROCESSED:
            mark_delivered(record)
            continue
        # Re-enqueue even when the job already has its deterministic ID. A
        # prior write may have failed after the job record was prepared but
        # before the INBOX transaction became durable.
        event_id = _enqueue_completion_event(record)
        if event_id:
            record["mailboxEventId"] = event_id
            record.pop("mailboxWriteError", None)
            _save_job(record)
        if event_id:
            recovered.append(event_id)
    return recovered


def reconcile_processed_deliveries(
    user_id: str,
    session_id: str,
    event_ids: Optional[list[str]] = None,
) -> int:
    """Project processed completion events back onto producer job rows."""
    from agent.mailbox import PROCESSED, get_mailbox_repository

    repository = get_mailbox_repository()
    reconciled = 0
    if event_ids:
        records = []
        for event_id in dict.fromkeys(event_ids):
            event = repository.get_event(user_id, session_id, event_id)
            if (
                event is None
                or event.status != PROCESSED
                or event.source.get("type") != "research_job"
            ):
                continue
            record = _get_job(
                user_id,
                session_id,
                event.source["id"],
            )
            if record is not None:
                records.append(record)
    else:
        records = _list_jobs(user_id, session_id)

    for record in records:
        if record.get("status") != "delivering":
            continue
        event = repository.get_event(
            user_id,
            session_id,
            record.get("mailboxEventId") or _completion_event_id(record),
        )
        if event is None or event.status != PROCESSED:
            continue
        mark_delivered(record)
        reconciled += 1
    return reconciled


def mark_delivered(record: Dict[str, Any]) -> None:
    record.update(status="delivered", deliveredAt=_now(), updatedAt=_now())
    record.pop("deliveryError", None)
    _save_job(record)


def register_delivery_handler(
    loop: asyncio.AbstractEventLoop,
    handler: DeliveryHandler,
) -> None:
    """Register the main FastAPI loop used for safe agent continuations."""
    global _delivery_loop, _delivery_handler
    with _delivery_lock:
        _delivery_loop = loop
        _delivery_handler = handler


def clear_delivery_handler() -> None:
    global _delivery_loop, _delivery_handler
    with _delivery_lock:
        _delivery_loop = None
        _delivery_handler = None


async def _deliver(record: Dict[str, Any], artifact: Dict[str, Any]) -> None:
    with _delivery_lock:
        loop = _delivery_loop
        handler = _delivery_handler
    if not loop or not handler or loop.is_closed():
        raise RuntimeError("Research delivery handler is not available")

    future = asyncio.run_coroutine_threadsafe(handler(dict(record), artifact), loop)
    await asyncio.wrap_future(future)


async def _notify_mailbox(record: Dict[str, Any]):
    from agent.mailbox_runtime import notify_session_mailbox

    return await notify_session_mailbox(
        record["userId"],
        record["sessionId"],
        event_ids=[record["mailboxEventId"]],
    )


def _publish_progress(record: Dict[str, Any], event: Dict[str, Any]) -> None:
    from streaming import skill_event_bus

    enriched = {
        **event,
        "jobId": record["jobId"],
        "artifactId": record["artifactId"],
    }
    queue = skill_event_bus.get_queue(record["sessionId"])
    if queue is not None:
        queue.put_nowait(enriched)


async def _run_job(record: Dict[str, Any], event_factory: EventFactory) -> None:
    task_id = async_tasks.begin(
        "research",
        {"job_id": record["jobId"], "session_id": record["sessionId"]},
    )
    try:
        record.update(status="running", startedAt=_now(), updatedAt=_now())
        _save_job(record)

        report = ""
        error = ""
        async for event in event_factory():
            if not isinstance(event, dict):
                continue
            if event.get("type") in ("research_step", "research_progress"):
                record["progress"] = {
                    "stepNumber": event.get("stepNumber", 0),
                    "content": event.get("content", ""),
                }
                record["updatedAt"] = _now()
                _save_job(record)
                _publish_progress(record, event)

            status = event.get("status")
            if status in ("success", "error"):
                content = event.get("content") or []
                text = content[0].get("text", "") if content and isinstance(content[0], dict) else ""
                if status == "success":
                    report = text
                else:
                    error = text or "Research agent failed"

        if error or not report:
            raise RuntimeError(error or "Research agent returned an empty report")

        completed_at = _now()
        record.update(
            status="completed",
            completedAt=completed_at,
            updatedAt=completed_at,
        )
        record.update(_save_report(record, report))
        artifact = _build_artifact(record, report)
        record["artifact"] = {key: value for key, value in artifact.items() if key != "content"}
        if _mailbox_write_enabled():
            record["mailboxEventId"] = _completion_event_id(record)

        # Persist every field needed by a mailbox consumer before publishing
        # the INBOX record. Once enqueue succeeds, the dispatcher may deliver
        # and mark this job delivered immediately; the worker must not write a
        # stale "completed" or "delivering" snapshot afterward.
        record.update(status="delivering", updatedAt=_now())
        _save_job(record)
        from agent.mailbox_runtime import mailbox_delivery_enabled

        publish = _publish_completion(record)
        if publish.status == CompletionPublishStatus.CANCELLED:
            record.update(
                status="cancelled",
                updatedAt=_now(),
                deliveryError=publish.error,
            )
            _save_job(record)
            logger.info(
                "[ResearchJob] Cancelled delivery to fenced session %s",
                record["sessionId"],
            )
            return
        if publish.status == CompletionPublishStatus.DURABLE:
            record["mailboxEventId"] = publish.event_id
            record.pop("mailboxWriteError", None)
        elif publish.status == CompletionPublishStatus.FAILED:
            record.pop("mailboxEventId", None)
            record["mailboxWriteError"] = publish.error
            if mailbox_delivery_enabled():
                record.update(
                    status="completed",
                    deliveryError=(
                        "Mailbox delivery is enabled but completion enqueue failed"
                    ),
                    updatedAt=_now(),
                )
                _save_job(record)
                return
        elif publish.status == CompletionPublishStatus.DISABLED:
            _save_job(record)

        try:
            if mailbox_delivery_enabled():
                if not record.get("mailboxEventId"):
                    raise RuntimeError(
                        "Mailbox delivery is enabled but completion enqueue failed"
                    )
                result = await _notify_mailbox(record)
                if result.dead:
                    raise RuntimeError("Mailbox delivery reached dead-letter state")
                # The mailbox handler owns the delivered projection. Another
                # coordinator may already be processing the event.
                return
            await _deliver(record, artifact)
        except Exception as exc:
            record.update(
                status="completed",
                deliveryError=str(exc),
                updatedAt=_now(),
            )
            _save_job(record)
            logger.exception("[ResearchJob] Delivery failed for %s", record["jobId"])
            return

        record.update(
            status="delivered",
            deliveredAt=_now(),
            updatedAt=_now(),
        )
        record.pop("deliveryError", None)
        _save_job(record)
    except Exception as exc:
        record.update(status="error", error=str(exc), updatedAt=_now())
        try:
            _save_job(record)
        except Exception:
            logger.exception("[ResearchJob] Failed to persist terminal error")
        logger.exception("[ResearchJob] Job %s failed", record["jobId"])
    finally:
        async_tasks.end(task_id)


def start_research_job(
    *,
    session_id: str,
    user_id: str,
    plan: str,
    artifact_id: str,
    event_factory: EventFactory,
    model_id: Optional[str] = None,
    request_type: str = "skill",
) -> Dict[str, Any]:
    """Persist and start one research job, returning its public start receipt."""
    job_id = uuid4().hex
    created_at = _now()
    conversation_epoch = 0
    if _mailbox_write_enabled():
        from agent.mailbox import get_mailbox_repository

        conversation_epoch = get_mailbox_repository().get_conversation_epoch(
            user_id,
            session_id,
        )
    record: Dict[str, Any] = {
        "jobId": job_id,
        "sessionId": session_id,
        "userId": user_id,
        "artifactId": artifact_id,
        "plan": plan,
        "status": "queued",
        "createdAt": created_at,
        "updatedAt": created_at,
        "modelId": model_id or "",
        "requestType": request_type,
        "conversationEpoch": conversation_epoch,
    }
    _save_job(record)

    thread = threading.Thread(
        target=lambda: asyncio.run(_run_job(record, event_factory)),
        name=f"research-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return {
        "status": "started",
        "job_id": job_id,
        "artifact_id": artifact_id,
    }
