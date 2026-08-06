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
from datetime import datetime, timezone
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", value)
    return cleaned or "unknown"


def _job_sk(session_id: str, job_id: str) -> str:
    return f"RESEARCH_JOB#{session_id}#{job_id}"


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


def _save_job(record: Dict[str, Any]) -> None:
    if _is_cloud_storage():
        table_name = os.environ["DYNAMODB_USERS_TABLE"]
        region = os.environ.get("AWS_REGION", "us-west-2")
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
        table_name = os.environ["DYNAMODB_USERS_TABLE"]
        region = os.environ.get("AWS_REGION", "us-west-2")
        table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        items = []
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
            response = table.query(**query_kwargs)
            items.extend(response.get("Items", []))
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                return items

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
        _save_job(record)

        try:
            record.update(status="delivering", updatedAt=_now())
            _save_job(record)
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
