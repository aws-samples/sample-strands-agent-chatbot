"""Wake AgentCore Runtime when a durable session mailbox receives work."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3

logger = logging.getLogger(__name__)

_token: str | None = None
_token_expires_at = 0.0
_TERMINAL_TTL_DAYS = 30
_DELEGATION_STALE_SECONDS = 180


def _secret() -> dict[str, str]:
    response = boto3.client("secretsmanager").get_secret_value(
        SecretId=os.environ["M2M_SECRET_ARN"],
    )
    return json.loads(response["SecretString"])


def _access_token() -> str:
    global _token, _token_expires_at
    if _token and time.time() < _token_expires_at - 60:
        return _token

    credentials = _secret()
    client_id = credentials["clientId"]
    client_secret = credentials["clientSecret"]
    form = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": "agentcore/invoke",
    }).encode()
    basic = base64.b64encode(
        f"{client_id}:{client_secret}".encode()
    ).decode()
    request = urllib.request.Request(
        os.environ["COGNITO_TOKEN_URL"],
        data=form,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read())

    _token = payload["access_token"]
    _token_expires_at = time.time() + int(payload.get("expires_in", 3600))
    return _token


def _wake(
    user_id: str,
    session_id: str,
    event_ids: list[str] | None = None,
) -> None:
    payload = json.dumps({
        "thread_id": session_id,
        "run_id": f"mailbox-dispatch-{int(time.time() * 1000)}",
        "messages": [],
        "tools": [],
        "context": [],
        "state": {
            "action": "drain_mailbox",
            "user_id": user_id,
            "event_ids": event_ids or [],
        },
    }).encode()
    request = urllib.request.Request(
        os.environ["AGENTCORE_RUNTIME_URL"],
        data=payload,
        headers={
            "Authorization": f"Bearer {_access_token()}",
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        },
        method="POST",
    )
    timeout_seconds = int(
        os.environ.get("RUNTIME_REQUEST_TIMEOUT_SECONDS", "540")
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        result = json.loads(response.read())
    if result.get("status") != "drained":
        raise RuntimeError(
            f"Mailbox remains pending for {user_id}/{session_id}: {result}"
        )


def _start_delegation(user_id: str, session_id: str, job_id: str) -> None:
    payload = json.dumps({
        "thread_id": session_id,
        "run_id": f"delegation-dispatch-{job_id}",
        "messages": [],
        "tools": [],
        "context": [],
        "state": {
            "action": "start_delegation",
            "user_id": user_id,
            "job_id": job_id,
        },
    }).encode()
    request = urllib.request.Request(
        os.environ["AGENTCORE_RUNTIME_URL"],
        data=payload,
        headers={
            "Authorization": f"Bearer {_access_token()}",
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read())
    if result.get("status") not in {"accepted", "ignored"}:
        raise RuntimeError(
            f"Delegation was not accepted for {user_id}/{session_id}: {result}"
        )


def _start_research(user_id: str, session_id: str, job_id: str) -> None:
    payload = json.dumps({
        "thread_id": session_id,
        "run_id": f"research-dispatch-{job_id}",
        "messages": [],
        "tools": [],
        "context": [],
        "state": {
            "action": "start_research",
            "user_id": user_id,
            "job_id": job_id,
        },
    }).encode()
    request = urllib.request.Request(
        os.environ["AGENTCORE_RUNTIME_URL"],
        data=payload,
        headers={
            "Authorization": f"Bearer {_access_token()}",
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read())
    if result.get("status") not in {"accepted", "ignored"}:
        raise RuntimeError(
            f"Research was not accepted for {user_id}/{session_id}: {result}"
        )


def _mailbox_target(record: dict[str, Any]) -> tuple[str, str, str] | None:
    if record.get("eventName") != "INSERT":
        return None
    image = record.get("dynamodb", {}).get("NewImage", {})
    if image.get("recordType", {}).get("S") != "INBOX":
        return None
    if image.get("status", {}).get("S") != "pending":
        return None
    user_id = image.get("userId", {}).get("S")
    session_id = image.get("sessionId", {}).get("S")
    mailbox_event_id = image.get("eventId", {}).get("S")
    if not user_id or not session_id or not mailbox_event_id:
        return None
    return user_id, session_id, mailbox_event_id


def _delegation_target(record: dict[str, Any]) -> tuple[str, str, str] | None:
    if record.get("eventName") not in {"INSERT", "MODIFY"}:
        return None
    image = record.get("dynamodb", {}).get("NewImage", {})
    if image.get("recordType", {}).get("S") != "DELEGATION_JOB":
        return None
    if image.get("workStatus", {}).get("S") != "queued":
        return None
    if image.get("desiredState", {}).get("S") != "running":
        return None
    user_id = image.get("userId", {}).get("S")
    session_id = image.get("sessionId", {}).get("S")
    job_id = image.get("jobId", {}).get("S")
    if not user_id or not session_id or not job_id:
        return None
    return user_id, session_id, job_id


def _research_target(record: dict[str, Any]) -> tuple[str, str, str] | None:
    if record.get("eventName") not in {"INSERT", "MODIFY"}:
        return None
    image = record.get("dynamodb", {}).get("NewImage", {})
    if image.get("recordType", {}).get("S") != "RESEARCH_JOB":
        return None
    if image.get("workStatus", {}).get("S") != "queued":
        return None
    if image.get("desiredState", {}).get("S") != "running":
        return None
    user_id = image.get("userId", {}).get("S")
    session_id = image.get("sessionId", {}).get("S")
    job_id = image.get("jobId", {}).get("S")
    if not user_id or not session_id or not job_id:
        return None
    return user_id, session_id, job_id


def _enqueue_wake(
    user_id: str,
    session_id: str,
    event_ids: list[str],
) -> None:
    body = json.dumps({
        "userId": user_id,
        "sessionId": session_id,
        "eventIds": event_ids,
    })
    target = f"{user_id}\n{session_id}".encode()
    deduplication = "\n".join(sorted(event_ids)).encode()
    boto3.client("sqs").send_message(
        QueueUrl=os.environ["WAKE_QUEUE_URL"],
        MessageBody=body,
        MessageGroupId=hashlib.sha256(target).hexdigest(),
        MessageDeduplicationId=hashlib.sha256(
            target + b"\n" + deduplication
        ).hexdigest(),
    )


def _enqueue_delegation(user_id: str, session_id: str, job_id: str) -> None:
    body = json.dumps({
        "kind": "delegation",
        "userId": user_id,
        "sessionId": session_id,
        "jobId": job_id,
    })
    target = f"{user_id}\n{session_id}".encode()
    boto3.client("sqs").send_message(
        QueueUrl=os.environ["WAKE_QUEUE_URL"],
        MessageBody=body,
        MessageGroupId=hashlib.sha256(target).hexdigest(),
        MessageDeduplicationId=hashlib.sha256(
            target + b"\ndelegation\n" + job_id.encode()
        ).hexdigest(),
    )


def _enqueue_research(user_id: str, session_id: str, job_id: str) -> None:
    body = json.dumps({
        "kind": "research",
        "userId": user_id,
        "sessionId": session_id,
        "jobId": job_id,
    })
    target = f"{user_id}\n{session_id}".encode()
    boto3.client("sqs").send_message(
        QueueUrl=os.environ["WAKE_QUEUE_URL"],
        MessageBody=body,
        MessageGroupId=hashlib.sha256(target).hexdigest(),
        MessageDeduplicationId=hashlib.sha256(
            target + b"\nresearch\n" + job_id.encode()
        ).hexdigest(),
    )


def _discard_deleted_session_wake(
    user_id: str,
    session_id: str,
    event_ids: list[str],
) -> bool:
    table_name = os.environ["ORCHESTRATION_TABLE_NAME"]
    session_key = f"USER#{user_id}#SESSION#{session_id}"
    client = boto3.client("dynamodb")
    state = client.get_item(
        TableName=table_name,
        Key={
            "sessionKey": {"S": session_key},
            "recordKey": {"S": "STATE"},
        },
        ConsistentRead=True,
        ProjectionExpression="deletedAt",
    ).get("Item")
    if not state or "deletedAt" not in state:
        return False

    now = datetime.now(timezone.utc)
    updated_at = now.isoformat()
    terminal_ttl = int(
        (now + timedelta(days=_TERMINAL_TTL_DAYS)).timestamp()
    )
    for event_id in event_ids:
        try:
            client.update_item(
                TableName=table_name,
                Key={
                    "sessionKey": {"S": session_key},
                    "recordKey": {"S": f"INBOX#{event_id}"},
                },
                UpdateExpression=(
                    "SET #status = :cancelled, processedAt = :updated, "
                    "updatedAt = :updated, lastError = :reason, #ttl = :ttl "
                    "REMOVE leaseOwner, leaseEpoch, eventLeaseUntil"
                ),
                ConditionExpression=(
                    "#status = :pending OR #status = :processing"
                ),
                ExpressionAttributeNames={
                    "#status": "status",
                    "#ttl": "ttl",
                },
                ExpressionAttributeValues={
                    ":cancelled": {"S": "cancelled"},
                    ":updated": {"S": updated_at},
                    ":reason": {"S": "Session deleted"},
                    ":ttl": {"N": str(terminal_ttl)},
                    ":pending": {"S": "pending"},
                    ":processing": {"S": "processing"},
                },
            )
        except Exception as error:
            code = getattr(error, "response", {}).get("Error", {}).get("Code")
            if code != "ConditionalCheckFailedException":
                raise
    return True


def _handle_stream(event: dict[str, Any]) -> dict[str, Any]:
    records_by_target: dict[
        tuple[str, str],
        dict[str, list[str]],
    ] = {}
    job_records: list[tuple[str, str, str, str, str]] = []
    for record in event.get("Records", []):
        target = _mailbox_target(record)
        if target:
            user_id, session_id, mailbox_event_id = target
            grouped = records_by_target.setdefault(
                (user_id, session_id),
                {"mailbox_event_ids": [], "stream_record_ids": []},
            )
            grouped["mailbox_event_ids"].append(mailbox_event_id)
            grouped["stream_record_ids"].append(record.get("eventID", ""))
            continue
        delegation = _delegation_target(record)
        if delegation:
            job_records.append(
                ("delegation", *delegation, record.get("eventID", ""))
            )
            continue
        research = _research_target(record)
        if research:
            job_records.append(
                ("research", *research, record.get("eventID", ""))
            )

    failures = []
    for (user_id, session_id), grouped in records_by_target.items():
        try:
            _enqueue_wake(
                user_id,
                session_id,
                grouped["mailbox_event_ids"],
            )
        except Exception:
            logger.exception(
                "Failed to enqueue mailbox wake for %s/%s",
                user_id,
                session_id,
            )
            failures.extend(
                {"itemIdentifier": record_id}
                for record_id in grouped["stream_record_ids"]
                if record_id
            )
    for kind, user_id, session_id, job_id, record_id in job_records:
        try:
            if kind == "delegation":
                _enqueue_delegation(user_id, session_id, job_id)
            else:
                _enqueue_research(user_id, session_id, job_id)
        except Exception:
            logger.exception(
                "Failed to enqueue %s %s for %s/%s",
                kind,
                job_id,
                user_id,
                session_id,
            )
            if record_id:
                failures.append({"itemIdentifier": record_id})

    return {"batchItemFailures": failures}


def _handle_sqs(event: dict[str, Any]) -> dict[str, Any]:
    records_by_target: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    invalid_records: list[dict[str, Any]] = []
    for record in event.get("Records", []):
        message_id = record.get("messageId", "")
        try:
            body = json.loads(record["body"])
            kind = str(body.get("kind") or "mailbox")
            if kind not in {"mailbox", "delegation", "research"}:
                raise ValueError(f"Unknown wake kind: {kind}")
            target = (kind, body["userId"], body["sessionId"])
            record["_mailboxEventIds"] = [
                str(event_id)
                for event_id in body.get("eventIds", [])
                if event_id
            ]
            record["_delegationJobId"] = str(body.get("jobId") or "")
            if kind in {"delegation", "research"} and not record["_delegationJobId"]:
                raise ValueError(f"{kind} wake requires jobId")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.exception("Invalid mailbox wake message %s", message_id)
            if message_id:
                invalid_records.append(record)
            continue
        records_by_target.setdefault(target, []).append(record)

    failures = []
    for record in invalid_records:
        _defer_retry(record)
        failures.append({"itemIdentifier": record["messageId"]})
    for (kind, user_id, session_id), records in records_by_target.items():
        try:
            if kind == "delegation":
                for job_id in sorted({
                    record["_delegationJobId"] for record in records
                }):
                    _start_delegation(user_id, session_id, job_id)
                continue
            if kind == "research":
                for job_id in sorted({
                    record["_delegationJobId"] for record in records
                }):
                    _start_research(user_id, session_id, job_id)
                continue
            event_ids = sorted({
                event_id
                for record in records
                for event_id in record.get("_mailboxEventIds", [])
            })
            if not _discard_deleted_session_wake(
                user_id,
                session_id,
                event_ids,
            ):
                _wake(user_id, session_id, event_ids)
        except Exception:
            logger.exception(
                "Failed to drain mailbox for %s/%s",
                user_id,
                session_id,
            )
            for record in records:
                _defer_retry(record)
                message_id = record.get("messageId")
                if message_id:
                    failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}


def _handle_reconcile() -> dict[str, Any]:
    """Re-enqueue queued and stale-running durable jobs."""
    client = boto3.client("dynamodb")
    table_name = os.environ["ORCHESTRATION_TABLE_NAME"]
    now = datetime.now(timezone.utc)
    cutoffs = {
        "queued": now.isoformat(),
        "running": (
            now - timedelta(seconds=_DELEGATION_STALE_SECONDS)
        ).isoformat(),
    }
    enqueued = 0
    for work_status, cutoff in cutoffs.items():
        exclusive_start_key = None
        while True:
            parameters: dict[str, Any] = {
                "TableName": table_name,
                "IndexName": "DelegationWorkIndex",
                "KeyConditionExpression": (
                    "workStatus = :workStatus AND heartbeatAt <= :cutoff"
                ),
                "FilterExpression": "desiredState = :running",
                "ExpressionAttributeValues": {
                    ":workStatus": {"S": work_status},
                    ":cutoff": {"S": cutoff},
                    ":running": {"S": "running"},
                },
                "ProjectionExpression": "recordType, userId, sessionId, jobId",
            }
            if exclusive_start_key:
                parameters["ExclusiveStartKey"] = exclusive_start_key
            response = client.query(**parameters)
            for item in response.get("Items", []):
                record_type = item.get("recordType", {}).get("S")
                enqueue = (
                    _enqueue_delegation
                    if record_type == "DELEGATION_JOB"
                    else _enqueue_research
                    if record_type == "RESEARCH_JOB"
                    else None
                )
                if enqueue is None:
                    continue
                enqueue(
                    item["userId"]["S"],
                    item["sessionId"]["S"],
                    item["jobId"]["S"],
                )
                enqueued += 1
            exclusive_start_key = response.get("LastEvaluatedKey")
            if not exclusive_start_key:
                break
    return {"status": "reconciled", "enqueued": enqueued}


def _defer_retry(record: dict[str, Any]) -> None:
    receipt_handle = record.get("receiptHandle")
    if not receipt_handle:
        return
    try:
        receive_count = int(
            record.get("attributes", {}).get("ApproximateReceiveCount", "1")
        )
    except (TypeError, ValueError):
        receive_count = 1
    visibility_timeout = min(300, 5 * (2 ** max(0, receive_count - 1)))
    try:
        boto3.client("sqs").change_message_visibility(
            QueueUrl=os.environ["WAKE_QUEUE_URL"],
            ReceiptHandle=receipt_handle,
            VisibilityTimeout=visibility_timeout,
        )
    except Exception:
        logger.exception(
            "Failed to shorten retry visibility for message %s",
            record.get("messageId", ""),
        )


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    if event.get("source") == "aws.events":
        return _handle_reconcile()
    records = event.get("Records", [])
    if records and records[0].get("eventSource") == "aws:sqs":
        return _handle_sqs(event)
    return _handle_stream(event)
