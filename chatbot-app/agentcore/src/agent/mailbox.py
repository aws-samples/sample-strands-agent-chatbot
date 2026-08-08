"""Durable per-session mailbox primitives.

Background workers may enqueue concurrently. Only a coordinator holding the
session lease may claim and acknowledge events that can mutate conversation
state.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer


SCHEMA_VERSION = 1
PENDING = "pending"
PROCESSING = "processing"
PROCESSED = "processed"
DEAD = "dead"
TERMINAL_TTL_DAYS = 30
SESSION_EVENT_TTL_DAYS = 7


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _session_key(user_id: str, session_id: str) -> str:
    return f"USER#{user_id}#SESSION#{session_id}"


def _event_key(event_id: str) -> str:
    return f"INBOX#{event_id}"


def _session_event_key(event_id: str) -> str:
    return f"OUTBOX#{event_id}"


def _error_code(error: Exception) -> str:
    response = getattr(error, "response", {})
    return response.get("Error", {}).get("Code", "")


def _is_condition_failure(error: Exception) -> bool:
    code = _error_code(error)
    if code == "ConditionalCheckFailedException":
        return True
    if code != "TransactionCanceledException":
        return False
    reasons = getattr(error, "response", {}).get("CancellationReasons", [])
    return any(reason.get("Code") == "ConditionalCheckFailed" for reason in reasons)


@dataclass(frozen=True)
class MailboxLease:
    owner: str
    epoch: int
    expires_at: int


class SessionDeletedError(RuntimeError):
    """Mailbox work cannot be added to a tombstoned session."""


@dataclass
class MailboxEvent:
    event_id: str
    event_type: str
    session_id: str
    user_id: str
    created_at: str
    available_at: str
    source: Dict[str, str]
    correlation: Dict[str, str] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)
    payload_ref: Optional[Dict[str, str]] = None
    visibility: str = "internal"
    schema_version: int = SCHEMA_VERSION
    status: str = PENDING
    attempts: int = 0
    lease_owner: Optional[str] = None
    lease_epoch: Optional[int] = None
    event_lease_until: Optional[int] = None
    last_error: Optional[str] = None
    processed_at: Optional[str] = None

    @classmethod
    def create(
        cls,
        *,
        event_id: str,
        event_type: str,
        session_id: str,
        user_id: str,
        source_type: str,
        source_id: str,
        correlation: Optional[Dict[str, str]] = None,
        payload: Optional[Dict[str, Any]] = None,
        payload_ref: Optional[Dict[str, str]] = None,
        visibility: str = "internal",
        now: Optional[datetime] = None,
    ) -> "MailboxEvent":
        timestamp = _iso(now or utc_now())
        return cls(
            event_id=event_id,
            event_type=event_type,
            session_id=session_id,
            user_id=user_id,
            created_at=timestamp,
            available_at=timestamp,
            source={"type": source_type, "id": source_id},
            correlation=dict(correlation or {}),
            payload=dict(payload or {}),
            payload_ref=dict(payload_ref) if payload_ref else None,
            visibility=visibility,
        )

    def to_record(self) -> Dict[str, Any]:
        record = {
            "sessionKey": _session_key(self.user_id, self.session_id),
            "recordKey": _event_key(self.event_id),
            "recordType": "INBOX",
            "schemaVersion": self.schema_version,
            "eventId": self.event_id,
            "eventType": self.event_type,
            "sessionId": self.session_id,
            "userId": self.user_id,
            "createdAt": self.created_at,
            "availableAt": self.available_at,
            "source": self.source,
            "correlation": self.correlation,
            "payload": self.payload,
            "payloadRef": self.payload_ref,
            "visibility": self.visibility,
            "status": self.status,
            "attempts": self.attempts,
            "leaseOwner": self.lease_owner,
            "leaseEpoch": self.lease_epoch,
            "eventLeaseUntil": self.event_lease_until,
            "lastError": self.last_error,
            "processedAt": self.processed_at,
        }
        return {key: value for key, value in record.items() if value is not None}

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "MailboxEvent":
        return cls(
            schema_version=int(record.get("schemaVersion", SCHEMA_VERSION)),
            event_id=record["eventId"],
            event_type=record["eventType"],
            session_id=record["sessionId"],
            user_id=record["userId"],
            created_at=record["createdAt"],
            available_at=record["availableAt"],
            source=record["source"],
            correlation=record.get("correlation", {}),
            payload=record.get("payload", {}),
            payload_ref=record.get("payloadRef"),
            visibility=record.get("visibility", "internal"),
            status=record.get("status", PENDING),
            attempts=int(record.get("attempts", 0)),
            lease_owner=record.get("leaseOwner"),
            lease_epoch=(
                int(record["leaseEpoch"]) if record.get("leaseEpoch") is not None else None
            ),
            event_lease_until=(
                int(record["eventLeaseUntil"])
                if record.get("eventLeaseUntil") is not None
                else None
            ),
            last_error=record.get("lastError"),
            processed_at=record.get("processedAt"),
        )


@dataclass(frozen=True)
class SessionEvent:
    event_id: str
    event_type: str
    session_id: str
    user_id: str
    created_at: str
    origin_event_id: str
    correlation: Dict[str, str] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        event_id: str,
        event_type: str,
        session_id: str,
        user_id: str,
        origin_event_id: str,
        correlation: Optional[Dict[str, str]] = None,
        payload: Optional[Dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> "SessionEvent":
        return cls(
            event_id=event_id,
            event_type=event_type,
            session_id=session_id,
            user_id=user_id,
            created_at=_iso(now or utc_now()),
            origin_event_id=origin_event_id,
            correlation=dict(correlation or {}),
            payload=dict(payload or {}),
        )

    def to_record(self, *, ttl: Optional[int] = None) -> Dict[str, Any]:
        record = {
            "sessionKey": _session_key(self.user_id, self.session_id),
            "recordKey": _session_event_key(self.event_id),
            "recordType": "OUTBOX",
            "schemaVersion": self.schema_version,
            "eventId": self.event_id,
            "eventType": self.event_type,
            "sessionId": self.session_id,
            "userId": self.user_id,
            "createdAt": self.created_at,
            "originEventId": self.origin_event_id,
            "correlation": self.correlation,
            "payload": self.payload,
            "ttl": ttl,
        }
        return {key: value for key, value in record.items() if value is not None}

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "SessionEvent":
        return cls(
            schema_version=int(record.get("schemaVersion", SCHEMA_VERSION)),
            event_id=record["eventId"],
            event_type=record["eventType"],
            session_id=record["sessionId"],
            user_id=record["userId"],
            created_at=record["createdAt"],
            origin_event_id=record["originEventId"],
            correlation=record.get("correlation", {}),
            payload=record.get("payload", {}),
        )


class MailboxRepository(ABC):
    @abstractmethod
    def enqueue(self, event: MailboxEvent) -> bool:
        """Insert once. Return False when the event already exists."""

    @abstractmethod
    def tombstone_session(
        self,
        user_id: str,
        session_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> None:
        """Block new work and fence any active coordinator."""

    @abstractmethod
    def is_session_deleted(self, user_id: str, session_id: str) -> bool:
        """Return whether the orchestration state has a delete tombstone."""

    @abstractmethod
    def acquire_lease(
        self,
        user_id: str,
        session_id: str,
        owner: str,
        *,
        lease_seconds: int,
        now: Optional[datetime] = None,
    ) -> Optional[MailboxLease]:
        """Acquire the session writer lease, or return None when held elsewhere."""

    @abstractmethod
    def release_lease(
        self,
        user_id: str,
        session_id: str,
        lease: MailboxLease,
    ) -> bool:
        """Release only when owner and fencing epoch still match."""

    @abstractmethod
    def renew_lease(
        self,
        user_id: str,
        session_id: str,
        lease: MailboxLease,
        *,
        lease_seconds: int,
        now: Optional[datetime] = None,
    ) -> Optional[MailboxLease]:
        """Extend a live lease without changing its fencing epoch."""

    @abstractmethod
    def claim_next(
        self,
        user_id: str,
        session_id: str,
        lease: MailboxLease,
        *,
        event_lease_seconds: int,
        now: Optional[datetime] = None,
    ) -> Optional[MailboxEvent]:
        """Claim the oldest eligible event under the current session lease."""

    @abstractmethod
    def acknowledge(
        self,
        event: MailboxEvent,
        lease: MailboxLease,
        *,
        session_events: Sequence[SessionEvent] = (),
        now: Optional[datetime] = None,
    ) -> bool:
        """Atomically mark processed and publish deterministic UI projections."""

    @abstractmethod
    def retry(
        self,
        event: MailboxEvent,
        lease: MailboxLease,
        error: str,
        *,
        delay_seconds: int,
        max_attempts: int,
        now: Optional[datetime] = None,
    ) -> str:
        """Release for retry or move to dead-letter. Return the new status."""

    @abstractmethod
    def list_events(self, user_id: str, session_id: str) -> list[MailboxEvent]:
        """List mailbox events in deterministic creation order."""

    @abstractmethod
    def list_session_events(
        self,
        user_id: str,
        session_id: str,
    ) -> list[SessionEvent]:
        """List durable frontend projections in deterministic creation order."""


class DynamoDBMailboxRepository(MailboxRepository):
    def __init__(
        self,
        table_name: str,
        *,
        region_name: str = "us-west-2",
        client: Any = None,
    ):
        self.table_name = table_name
        self.client = client or boto3.client("dynamodb", region_name=region_name)
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

    def _serialize(self, value: Dict[str, Any]) -> Dict[str, Any]:
        # DynamoDB rejects Python floats. A JSON round trip converts them to
        # Decimal while also proving the envelope is JSON-compatible.
        normalized = json.loads(json.dumps(value), parse_float=Decimal)
        return {key: self._serializer.serialize(item) for key, item in normalized.items()}

    def _deserialize(self, value: Dict[str, Any]) -> Dict[str, Any]:
        raw = {key: self._deserializer.deserialize(item) for key, item in value.items()}

        def normalize(item: Any) -> Any:
            if isinstance(item, Decimal):
                return int(item) if item == item.to_integral_value() else float(item)
            if isinstance(item, dict):
                return {key: normalize(child) for key, child in item.items()}
            if isinstance(item, list):
                return [normalize(child) for child in item]
            return item

        return normalize(raw)

    def _key(self, user_id: str, session_id: str, record_key: str) -> Dict[str, Any]:
        return self._serialize({
            "sessionKey": _session_key(user_id, session_id),
            "recordKey": record_key,
        })

    def enqueue(self, event: MailboxEvent) -> bool:
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "ConditionCheck": {
                            "TableName": self.table_name,
                            "Key": self._key(
                                event.user_id,
                                event.session_id,
                                "STATE",
                            ),
                            "ConditionExpression": "attribute_not_exists(deletedAt)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": self._serialize(event.to_record()),
                            "ConditionExpression": "attribute_not_exists(recordKey)",
                        }
                    },
                ]
            )
            return True
        except Exception as error:
            if _is_condition_failure(error):
                if self.is_session_deleted(event.user_id, event.session_id):
                    raise SessionDeletedError(
                        f"Session {event.session_id} has been deleted"
                    ) from error
                return False
            raise

    def tombstone_session(
        self,
        user_id: str,
        session_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> None:
        current = now or utc_now()
        self.client.update_item(
            TableName=self.table_name,
            Key=self._key(user_id, session_id, "STATE"),
            UpdateExpression=(
                "SET deletedAt = :deleted, updatedAt = :deleted, "
                "#status = :status "
                "REMOVE leaseOwner, leaseUntil"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=self._serialize({
                ":deleted": _iso(current),
                ":status": "deleted",
            }),
        )

    def is_session_deleted(self, user_id: str, session_id: str) -> bool:
        response = self.client.get_item(
            TableName=self.table_name,
            Key=self._key(user_id, session_id, "STATE"),
            ConsistentRead=True,
            ProjectionExpression="deletedAt",
        )
        return bool(response.get("Item"))

    def acquire_lease(
        self,
        user_id: str,
        session_id: str,
        owner: str,
        *,
        lease_seconds: int,
        now: Optional[datetime] = None,
    ) -> Optional[MailboxLease]:
        current = now or utc_now()
        now_epoch = int(current.timestamp())
        expires_at = now_epoch + lease_seconds
        try:
            response = self.client.update_item(
                TableName=self.table_name,
                Key=self._key(user_id, session_id, "STATE"),
                UpdateExpression=(
                    "SET leaseOwner = :owner, leaseUntil = :until, "
                    "leaseEpoch = if_not_exists(leaseEpoch, :zero) + :one, "
                    "#version = if_not_exists(#version, :zero) + :one, "
                    "updatedAt = :updated"
                ),
                ConditionExpression=(
                    "attribute_not_exists(deletedAt) AND ("
                    "attribute_not_exists(leaseUntil) OR leaseUntil < :now "
                    "OR leaseOwner = :owner)"
                ),
                ExpressionAttributeNames={"#version": "version"},
                ExpressionAttributeValues=self._serialize({
                    ":owner": owner,
                    ":until": expires_at,
                    ":zero": 0,
                    ":one": 1,
                    ":now": now_epoch,
                    ":updated": _iso(current),
                }),
                ReturnValues="ALL_NEW",
            )
        except Exception as error:
            if _error_code(error) == "ConditionalCheckFailedException":
                return None
            raise
        attributes = self._deserialize(response["Attributes"])
        return MailboxLease(
            owner=owner,
            epoch=int(attributes["leaseEpoch"]),
            expires_at=expires_at,
        )

    def release_lease(
        self,
        user_id: str,
        session_id: str,
        lease: MailboxLease,
    ) -> bool:
        try:
            self.client.update_item(
                TableName=self.table_name,
                Key=self._key(user_id, session_id, "STATE"),
                UpdateExpression="REMOVE leaseOwner, leaseUntil",
                ConditionExpression="leaseOwner = :owner AND leaseEpoch = :epoch",
                ExpressionAttributeValues=self._serialize({
                    ":owner": lease.owner,
                    ":epoch": lease.epoch,
                }),
            )
            return True
        except Exception as error:
            if _error_code(error) == "ConditionalCheckFailedException":
                return False
            raise

    def renew_lease(
        self,
        user_id: str,
        session_id: str,
        lease: MailboxLease,
        *,
        lease_seconds: int,
        now: Optional[datetime] = None,
    ) -> Optional[MailboxLease]:
        current = now or utc_now()
        now_epoch = int(current.timestamp())
        expires_at = now_epoch + lease_seconds
        try:
            self.client.update_item(
                TableName=self.table_name,
                Key=self._key(user_id, session_id, "STATE"),
                UpdateExpression="SET leaseUntil = :until, updatedAt = :updated",
                ConditionExpression=(
                    "leaseOwner = :owner AND leaseEpoch = :epoch "
                    "AND leaseUntil >= :now"
                ),
                ExpressionAttributeValues=self._serialize({
                    ":owner": lease.owner,
                    ":epoch": lease.epoch,
                    ":now": now_epoch,
                    ":until": expires_at,
                    ":updated": _iso(current),
                }),
            )
            return MailboxLease(
                owner=lease.owner,
                epoch=lease.epoch,
                expires_at=expires_at,
            )
        except Exception as error:
            if _is_condition_failure(error):
                return None
            raise

    def list_events(self, user_id: str, session_id: str) -> list[MailboxEvent]:
        items: list[Dict[str, Any]] = []
        start_key = None
        while True:
            kwargs: Dict[str, Any] = {
                "TableName": self.table_name,
                "KeyConditionExpression": (
                    "sessionKey = :session AND begins_with(recordKey, :prefix)"
                ),
                "ExpressionAttributeValues": self._serialize({
                    ":session": _session_key(user_id, session_id),
                    ":prefix": "INBOX#",
                }),
                "ConsistentRead": True,
            }
            if start_key:
                kwargs["ExclusiveStartKey"] = start_key
            response = self.client.query(**kwargs)
            items.extend(self._deserialize(item) for item in response.get("Items", []))
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                break
        events = [MailboxEvent.from_record(item) for item in items]
        return sorted(events, key=lambda item: (item.created_at, item.event_id))

    def list_session_events(
        self,
        user_id: str,
        session_id: str,
    ) -> list[SessionEvent]:
        items: list[Dict[str, Any]] = []
        start_key = None
        while True:
            kwargs: Dict[str, Any] = {
                "TableName": self.table_name,
                "KeyConditionExpression": (
                    "sessionKey = :session AND begins_with(recordKey, :prefix)"
                ),
                "ExpressionAttributeValues": self._serialize({
                    ":session": _session_key(user_id, session_id),
                    ":prefix": "OUTBOX#",
                }),
                "ConsistentRead": True,
            }
            if start_key:
                kwargs["ExclusiveStartKey"] = start_key
            response = self.client.query(**kwargs)
            items.extend(self._deserialize(item) for item in response.get("Items", []))
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                break
        events = [SessionEvent.from_record(item) for item in items]
        return sorted(events, key=lambda item: (item.created_at, item.event_id))

    def claim_next(
        self,
        user_id: str,
        session_id: str,
        lease: MailboxLease,
        *,
        event_lease_seconds: int,
        now: Optional[datetime] = None,
    ) -> Optional[MailboxEvent]:
        current = now or utc_now()
        now_epoch = int(current.timestamp())
        now_iso = _iso(current)
        candidates = [
            event
            for event in self.list_events(user_id, session_id)
            if (
                event.status == PENDING and event.available_at <= now_iso
            ) or (
                event.status == PROCESSING
                and (event.event_lease_until or 0) < now_epoch
            )
        ]
        for event in candidates:
            try:
                self.client.transact_write_items(
                    TransactItems=[
                        {
                            "ConditionCheck": {
                                "TableName": self.table_name,
                                "Key": self._key(user_id, session_id, "STATE"),
                                "ConditionExpression": (
                                    "leaseOwner = :owner AND leaseEpoch = :epoch "
                                    "AND leaseUntil >= :now_epoch"
                                ),
                                "ExpressionAttributeValues": self._serialize({
                                    ":owner": lease.owner,
                                    ":epoch": lease.epoch,
                                    ":now_epoch": now_epoch,
                                }),
                            }
                        },
                        {
                            "Update": {
                                "TableName": self.table_name,
                                "Key": self._key(
                                    user_id,
                                    session_id,
                                    _event_key(event.event_id),
                                ),
                                "UpdateExpression": (
                                    "SET #status = :processing, leaseOwner = :owner, "
                                    "leaseEpoch = :epoch, eventLeaseUntil = :until, "
                                    "attempts = if_not_exists(attempts, :zero) + :one, "
                                    "updatedAt = :updated"
                                ),
                                "ConditionExpression": (
                                    "(#status = :pending AND availableAt <= :now_iso) OR "
                                    "(#status = :processing AND eventLeaseUntil < :now_epoch)"
                                ),
                                "ExpressionAttributeNames": {"#status": "status"},
                                "ExpressionAttributeValues": self._serialize({
                                    ":processing": PROCESSING,
                                    ":pending": PENDING,
                                    ":owner": lease.owner,
                                    ":epoch": lease.epoch,
                                    ":until": now_epoch + event_lease_seconds,
                                    ":zero": 0,
                                    ":one": 1,
                                    ":updated": now_iso,
                                    ":now_iso": now_iso,
                                    ":now_epoch": now_epoch,
                                }),
                            }
                        },
                    ]
                )
                return replace(
                    event,
                    status=PROCESSING,
                    attempts=event.attempts + 1,
                    lease_owner=lease.owner,
                    lease_epoch=lease.epoch,
                    event_lease_until=now_epoch + event_lease_seconds,
                )
            except Exception as error:
                if not _is_condition_failure(error):
                    raise
        return None

    def acknowledge(
        self,
        event: MailboxEvent,
        lease: MailboxLease,
        *,
        session_events: Sequence[SessionEvent] = (),
        now: Optional[datetime] = None,
    ) -> bool:
        current = now or utc_now()
        now_epoch = int(current.timestamp())
        if len(session_events) > 98:
            raise ValueError("A mailbox acknowledgement supports at most 98 session events")
        projection_ttl = int(
            (current + timedelta(days=SESSION_EVENT_TTL_DAYS)).timestamp()
        )
        projections = [
            {
                "Put": {
                    "TableName": self.table_name,
                    "Item": self._serialize(item.to_record(ttl=projection_ttl)),
                }
            }
            for item in session_events
        ]
        try:
            self.client.transact_write_items(
                TransactItems=[
                    self._lease_condition(event.user_id, event.session_id, lease, now_epoch),
                    {
                        "Update": {
                            "TableName": self.table_name,
                            "Key": self._key(
                                event.user_id,
                                event.session_id,
                                _event_key(event.event_id),
                            ),
                            "UpdateExpression": (
                                "SET #status = :processed, processedAt = :processed_at, "
                                "updatedAt = :processed_at, #ttl = :ttl "
                                "REMOVE leaseOwner, leaseEpoch, eventLeaseUntil, lastError"
                            ),
                            "ConditionExpression": (
                                "#status = :processing AND leaseOwner = :owner "
                                "AND leaseEpoch = :epoch"
                            ),
                            "ExpressionAttributeNames": {
                                "#status": "status",
                                "#ttl": "ttl",
                            },
                            "ExpressionAttributeValues": self._serialize({
                                ":processed": PROCESSED,
                                ":processing": PROCESSING,
                                ":processed_at": _iso(current),
                                ":ttl": int(
                                    (
                                        current
                                        + timedelta(days=TERMINAL_TTL_DAYS)
                                    ).timestamp()
                                ),
                                ":owner": lease.owner,
                                ":epoch": lease.epoch,
                            }),
                        }
                    },
                    *projections,
                ]
            )
            return True
        except Exception as error:
            if _is_condition_failure(error):
                return False
            raise

    def retry(
        self,
        event: MailboxEvent,
        lease: MailboxLease,
        error: str,
        *,
        delay_seconds: int,
        max_attempts: int,
        now: Optional[datetime] = None,
    ) -> str:
        current = now or utc_now()
        status = DEAD if event.attempts >= max_attempts else PENDING
        values: Dict[str, Any] = {
            ":next_status": status,
            ":processing": PROCESSING,
            ":available": _iso(current + timedelta(seconds=delay_seconds)),
            ":updated": _iso(current),
            ":error": error[:2000],
            ":owner": lease.owner,
            ":epoch": lease.epoch,
        }
        update = (
            "SET #status = :next_status, availableAt = :available, "
            "updatedAt = :updated, lastError = :error "
        )
        if status == DEAD:
            update += ", #ttl = :ttl "
            values[":ttl"] = int(
                (current + timedelta(days=TERMINAL_TTL_DAYS)).timestamp()
            )
        update += "REMOVE leaseOwner, leaseEpoch, eventLeaseUntil"
        attribute_names = {"#status": "status"}
        if status == DEAD:
            attribute_names["#ttl"] = "ttl"
        try:
            self.client.transact_write_items(
                TransactItems=[
                    self._lease_condition(
                        event.user_id,
                        event.session_id,
                        lease,
                        int(current.timestamp()),
                    ),
                    {
                        "Update": {
                            "TableName": self.table_name,
                            "Key": self._key(
                                event.user_id,
                                event.session_id,
                                _event_key(event.event_id),
                            ),
                            "UpdateExpression": update,
                            "ConditionExpression": (
                                "#status = :processing AND leaseOwner = :owner "
                                "AND leaseEpoch = :epoch"
                            ),
                            "ExpressionAttributeNames": attribute_names,
                            "ExpressionAttributeValues": self._serialize(values),
                        }
                    },
                ]
            )
            return status
        except Exception as exc:
            if _is_condition_failure(exc):
                return event.status
            raise

    def _lease_condition(
        self,
        user_id: str,
        session_id: str,
        lease: MailboxLease,
        now_epoch: int,
    ) -> Dict[str, Any]:
        return {
            "ConditionCheck": {
                "TableName": self.table_name,
                "Key": self._key(user_id, session_id, "STATE"),
                "ConditionExpression": (
                    "leaseOwner = :owner AND leaseEpoch = :epoch "
                    "AND leaseUntil >= :now"
                ),
                "ExpressionAttributeValues": self._serialize({
                    ":owner": lease.owner,
                    ":epoch": lease.epoch,
                    ":now": now_epoch,
                }),
            }
        }


class FileMailboxRepository(MailboxRepository):
    """Atomic local-development store with the same fencing semantics."""

    def __init__(self, storage_dir: Path):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, user_id: str, session_id: str) -> Path:
        digest = sha256(_session_key(user_id, session_id).encode("utf-8")).hexdigest()
        return self.storage_dir / f"{digest}.json"

    def _load(self, user_id: str, session_id: str) -> Dict[str, Any]:
        path = self._path(user_id, session_id)
        if not path.exists():
            return {
                "state": {"leaseEpoch": 0, "version": 0},
                "events": {},
                "sessionEvents": {},
            }
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("sessionEvents", {})
        return data

    def _save(self, user_id: str, session_id: str, data: Dict[str, Any]) -> None:
        path = self._path(user_id, session_id)
        fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def enqueue(self, event: MailboxEvent) -> bool:
        with self._lock:
            data = self._load(event.user_id, event.session_id)
            if data["state"].get("deletedAt"):
                raise SessionDeletedError(
                    f"Session {event.session_id} has been deleted"
                )
            if event.event_id in data["events"]:
                return False
            data["events"][event.event_id] = event.to_record()
            self._save(event.user_id, event.session_id, data)
            return True

    def tombstone_session(
        self,
        user_id: str,
        session_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> None:
        current = now or utc_now()
        with self._lock:
            data = self._load(user_id, session_id)
            data["state"].update({
                "deletedAt": _iso(current),
                "updatedAt": _iso(current),
                "status": "deleted",
            })
            data["state"].pop("leaseOwner", None)
            data["state"].pop("leaseUntil", None)
            self._save(user_id, session_id, data)

    def is_session_deleted(self, user_id: str, session_id: str) -> bool:
        with self._lock:
            return bool(
                self._load(user_id, session_id)["state"].get("deletedAt")
            )

    def acquire_lease(
        self,
        user_id: str,
        session_id: str,
        owner: str,
        *,
        lease_seconds: int,
        now: Optional[datetime] = None,
    ) -> Optional[MailboxLease]:
        current = now or utc_now()
        now_epoch = int(current.timestamp())
        with self._lock:
            data = self._load(user_id, session_id)
            state = data["state"]
            if state.get("deletedAt"):
                return None
            if (
                state.get("leaseUntil", 0) >= now_epoch
                and state.get("leaseOwner") != owner
            ):
                return None
            state["leaseOwner"] = owner
            state["leaseUntil"] = now_epoch + lease_seconds
            state["leaseEpoch"] = int(state.get("leaseEpoch", 0)) + 1
            state["version"] = int(state.get("version", 0)) + 1
            state["updatedAt"] = _iso(current)
            self._save(user_id, session_id, data)
            return MailboxLease(
                owner=owner,
                epoch=state["leaseEpoch"],
                expires_at=state["leaseUntil"],
            )

    def release_lease(
        self,
        user_id: str,
        session_id: str,
        lease: MailboxLease,
    ) -> bool:
        with self._lock:
            data = self._load(user_id, session_id)
            state = data["state"]
            if (
                state.get("leaseOwner") != lease.owner
                or state.get("leaseEpoch") != lease.epoch
            ):
                return False
            state.pop("leaseOwner", None)
            state.pop("leaseUntil", None)
            self._save(user_id, session_id, data)
            return True

    def renew_lease(
        self,
        user_id: str,
        session_id: str,
        lease: MailboxLease,
        *,
        lease_seconds: int,
        now: Optional[datetime] = None,
    ) -> Optional[MailboxLease]:
        current = now or utc_now()
        now_epoch = int(current.timestamp())
        with self._lock:
            data = self._load(user_id, session_id)
            state = data["state"]
            if (
                state.get("leaseOwner") != lease.owner
                or state.get("leaseEpoch") != lease.epoch
                or state.get("leaseUntil", 0) < now_epoch
            ):
                return None
            state["leaseUntil"] = now_epoch + lease_seconds
            state["updatedAt"] = _iso(current)
            self._save(user_id, session_id, data)
            return MailboxLease(
                owner=lease.owner,
                epoch=lease.epoch,
                expires_at=state["leaseUntil"],
            )

    def list_events(self, user_id: str, session_id: str) -> list[MailboxEvent]:
        with self._lock:
            data = self._load(user_id, session_id)
            events = [
                MailboxEvent.from_record(record)
                for record in data["events"].values()
            ]
        return sorted(events, key=lambda item: (item.created_at, item.event_id))

    def list_session_events(
        self,
        user_id: str,
        session_id: str,
    ) -> list[SessionEvent]:
        with self._lock:
            data = self._load(user_id, session_id)
            events = [
                SessionEvent.from_record(record)
                for record in data["sessionEvents"].values()
            ]
        return sorted(events, key=lambda item: (item.created_at, item.event_id))

    def claim_next(
        self,
        user_id: str,
        session_id: str,
        lease: MailboxLease,
        *,
        event_lease_seconds: int,
        now: Optional[datetime] = None,
    ) -> Optional[MailboxEvent]:
        current = now or utc_now()
        now_epoch = int(current.timestamp())
        now_iso = _iso(current)
        with self._lock:
            data = self._load(user_id, session_id)
            state = data["state"]
            if (
                state.get("leaseOwner") != lease.owner
                or state.get("leaseEpoch") != lease.epoch
                or state.get("leaseUntil", 0) < now_epoch
            ):
                return None
            records = sorted(
                data["events"].values(),
                key=lambda item: (item["createdAt"], item["eventId"]),
            )
            for record in records:
                eligible = (
                    record["status"] == PENDING
                    and record["availableAt"] <= now_iso
                ) or (
                    record["status"] == PROCESSING
                    and record.get("eventLeaseUntil", 0) < now_epoch
                )
                if not eligible:
                    continue
                record.update({
                    "status": PROCESSING,
                    "leaseOwner": lease.owner,
                    "leaseEpoch": lease.epoch,
                    "eventLeaseUntil": now_epoch + event_lease_seconds,
                    "attempts": int(record.get("attempts", 0)) + 1,
                    "updatedAt": now_iso,
                })
                self._save(user_id, session_id, data)
                return MailboxEvent.from_record(record)
        return None

    def acknowledge(
        self,
        event: MailboxEvent,
        lease: MailboxLease,
        *,
        session_events: Sequence[SessionEvent] = (),
        now: Optional[datetime] = None,
    ) -> bool:
        current = now or utc_now()
        with self._lock:
            data = self._load(event.user_id, event.session_id)
            record = data["events"].get(event.event_id)
            if not self._owns(
                record,
                data["state"],
                lease,
                now_epoch=int(current.timestamp()),
            ):
                return False
            record.update({
                "status": PROCESSED,
                "processedAt": _iso(current),
                "updatedAt": _iso(current),
                "ttl": int(
                    (current + timedelta(days=TERMINAL_TTL_DAYS)).timestamp()
                ),
            })
            self._clear_event_lease(record)
            projection_ttl = int(
                (current + timedelta(days=SESSION_EVENT_TTL_DAYS)).timestamp()
            )
            for item in session_events:
                data["sessionEvents"][item.event_id] = item.to_record(
                    ttl=projection_ttl
                )
            self._save(event.user_id, event.session_id, data)
            return True

    def retry(
        self,
        event: MailboxEvent,
        lease: MailboxLease,
        error: str,
        *,
        delay_seconds: int,
        max_attempts: int,
        now: Optional[datetime] = None,
    ) -> str:
        current = now or utc_now()
        with self._lock:
            data = self._load(event.user_id, event.session_id)
            record = data["events"].get(event.event_id)
            if not self._owns(
                record,
                data["state"],
                lease,
                now_epoch=int(current.timestamp()),
            ):
                return event.status
            status = DEAD if int(record.get("attempts", 0)) >= max_attempts else PENDING
            record.update({
                "status": status,
                "availableAt": _iso(current + timedelta(seconds=delay_seconds)),
                "updatedAt": _iso(current),
                "lastError": error[:2000],
            })
            if status == DEAD:
                record["ttl"] = int(
                    (current + timedelta(days=TERMINAL_TTL_DAYS)).timestamp()
                )
            self._clear_event_lease(record)
            self._save(event.user_id, event.session_id, data)
            return status

    @staticmethod
    def _owns(
        record: Optional[Dict[str, Any]],
        state: Dict[str, Any],
        lease: MailboxLease,
        *,
        now_epoch: int,
    ) -> bool:
        return bool(
            record
            and record.get("status") == PROCESSING
            and record.get("leaseOwner") == lease.owner
            and record.get("leaseEpoch") == lease.epoch
            and not state.get("deletedAt")
            and state.get("leaseOwner") == lease.owner
            and state.get("leaseEpoch") == lease.epoch
            and state.get("leaseUntil", 0) >= now_epoch
        )

    @staticmethod
    def _clear_event_lease(record: Dict[str, Any]) -> None:
        record.pop("leaseOwner", None)
        record.pop("leaseEpoch", None)
        record.pop("eventLeaseUntil", None)


def create_mailbox_repository() -> MailboxRepository:
    table_name = os.environ.get("SESSION_ORCHESTRATION_TABLE")
    if table_name:
        return DynamoDBMailboxRepository(
            table_name,
            region_name=os.environ.get("AWS_REGION", "us-west-2"),
        )

    from agent.factory.session_manager_factory import get_sessions_dir

    return FileMailboxRepository(get_sessions_dir() / "mailbox")


_repository_instance: Optional[MailboxRepository] = None
_repository_lock = threading.Lock()


def get_mailbox_repository() -> MailboxRepository:
    global _repository_instance
    if _repository_instance is None:
        with _repository_lock:
            if _repository_instance is None:
                _repository_instance = create_mailbox_repository()
    return _repository_instance


def reset_mailbox_repository() -> None:
    """Reset the process cache for tests or configuration reloads."""
    global _repository_instance
    with _repository_lock:
        _repository_instance = None
