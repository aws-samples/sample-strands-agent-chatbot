"""Wake AgentCore Runtime when a durable session mailbox receives work."""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from typing import Any

import boto3


logger = logging.getLogger(__name__)

_token: str | None = None
_token_expires_at = 0.0


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


def _wake(user_id: str, session_id: str) -> None:
    payload = json.dumps({
        "thread_id": session_id,
        "run_id": f"mailbox-dispatch-{int(time.time() * 1000)}",
        "messages": [],
        "tools": [],
        "context": [],
        "state": {
            "action": "drain_mailbox",
            "user_id": user_id,
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
    if result.get("status") != "drained":
        raise RuntimeError(
            f"Mailbox remains pending for {user_id}/{session_id}: {result}"
        )


def _mailbox_target(record: dict[str, Any]) -> tuple[str, str] | None:
    if record.get("eventName") != "INSERT":
        return None
    image = record.get("dynamodb", {}).get("NewImage", {})
    if image.get("recordType", {}).get("S") != "INBOX":
        return None
    if image.get("status", {}).get("S") != "pending":
        return None
    user_id = image.get("userId", {}).get("S")
    session_id = image.get("sessionId", {}).get("S")
    if not user_id or not session_id:
        return None
    return user_id, session_id


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    records_by_target: dict[tuple[str, str], list[str]] = {}
    for record in event.get("Records", []):
        target = _mailbox_target(record)
        if target:
            records_by_target.setdefault(target, []).append(
                record.get("eventID", ""),
            )

    failures = []
    for (user_id, session_id), event_ids in records_by_target.items():
        try:
            _wake(user_id, session_id)
        except Exception:
            logger.exception(
                "Failed to drain mailbox for %s/%s",
                user_id,
                session_id,
            )
            failures.extend(
                {"itemIdentifier": event_id}
                for event_id in event_ids
                if event_id
            )

    return {"batchItemFailures": failures}
