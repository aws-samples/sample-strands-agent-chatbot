from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from agent.mailbox import (
    CANCELLED,
    DEAD,
    PENDING,
    PROCESSED,
    DynamoDBMailboxRepository,
    FileMailboxRepository,
    MailboxEvent,
    SessionDeletedError,
    SessionSupersededError,
    SessionEvent,
)


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def event(event_id: str, *, now: datetime = NOW) -> MailboxEvent:
    return MailboxEvent.create(
        event_id=event_id,
        event_type="async_result.ready",
        session_id="session-1",
        user_id="user-1",
        source_type="test",
        source_id=event_id,
        now=now,
    )


def test_enqueue_is_idempotent(tmp_path):
    repository = FileMailboxRepository(tmp_path)
    item = event("event-1")

    assert repository.enqueue(item) is True
    assert repository.enqueue(item) is False
    assert [stored.event_id for stored in repository.list_events("user-1", "session-1")] == [
        "event-1"
    ]
    assert repository.get_event("user-1", "session-1", "event-1") == item
    assert repository.get_event("user-1", "session-1", "missing") is None


def test_deleted_session_rejects_new_events_and_leases(tmp_path):
    repository = FileMailboxRepository(tmp_path)
    repository.tombstone_session("user-1", "session-1", now=NOW)

    with pytest.raises(SessionDeletedError):
        repository.enqueue(event("event-1"))
    assert repository.acquire_lease(
        "user-1",
        "session-1",
        "worker-1",
        lease_seconds=30,
        now=NOW,
    ) is None


def test_truncate_epoch_rejects_stale_events_and_accepts_current_work(tmp_path):
    repository = FileMailboxRepository(tmp_path)
    stale = event("stale")

    assert repository.advance_conversation_epoch(
        "user-1",
        "session-1",
        now=NOW,
    ) == 1
    with pytest.raises(SessionSupersededError):
        repository.enqueue(stale)

    current = event("current")
    current.conversation_epoch = 1
    assert repository.enqueue(current) is True


def test_truncate_epoch_fences_claimed_event_and_cancels_it(tmp_path):
    repository = FileMailboxRepository(tmp_path)
    repository.enqueue(event("event-1"))
    lease = repository.acquire_lease(
        "user-1",
        "session-1",
        "worker-1",
        lease_seconds=30,
        now=NOW,
    )
    claimed = repository.claim_next(
        "user-1",
        "session-1",
        lease,
        event_lease_seconds=30,
        now=NOW,
    )

    repository.advance_conversation_epoch(
        "user-1",
        "session-1",
        now=NOW + timedelta(seconds=1),
    )

    assert repository.acknowledge(
        claimed,
        lease,
        now=NOW + timedelta(seconds=1),
    ) is False
    assert repository.get_event(
        "user-1",
        "session-1",
        "event-1",
    ).status == CANCELLED


def test_tombstone_fences_claimed_event_acknowledgement(tmp_path):
    repository = FileMailboxRepository(tmp_path)
    repository.enqueue(event("event-1"))
    lease = repository.acquire_lease(
        "user-1", "session-1", "worker-1", lease_seconds=30, now=NOW
    )
    claimed = repository.claim_next(
        "user-1",
        "session-1",
        lease,
        event_lease_seconds=30,
        now=NOW,
    )

    repository.tombstone_session(
        "user-1",
        "session-1",
        now=NOW + timedelta(seconds=1),
    )

    assert repository.acknowledge(
        claimed,
        lease,
        now=NOW + timedelta(seconds=1),
    ) is False
    assert repository.list_events("user-1", "session-1")[0].status == "processing"


def test_expired_session_lease_cannot_acknowledge(tmp_path):
    repository = FileMailboxRepository(tmp_path)
    repository.enqueue(event("event-1"))
    lease = repository.acquire_lease(
        "user-1", "session-1", "worker-1", lease_seconds=10, now=NOW
    )
    claimed = repository.claim_next(
        "user-1",
        "session-1",
        lease,
        event_lease_seconds=30,
        now=NOW,
    )

    assert repository.acknowledge(
        claimed,
        lease,
        now=NOW + timedelta(seconds=11),
    ) is False


def test_claims_events_in_creation_order(tmp_path):
    repository = FileMailboxRepository(tmp_path)
    repository.enqueue(event("later", now=NOW + timedelta(seconds=1)))
    repository.enqueue(event("earlier"))
    lease = repository.acquire_lease(
        "user-1", "session-1", "worker-1", lease_seconds=30, now=NOW
    )

    claimed = repository.claim_next(
        "user-1",
        "session-1",
        lease,
        event_lease_seconds=30,
        now=NOW + timedelta(seconds=2),
    )

    assert claimed.event_id == "earlier"
    assert claimed.attempts == 1


def test_only_one_owner_holds_a_live_session_lease(tmp_path):
    repository = FileMailboxRepository(tmp_path)

    first = repository.acquire_lease(
        "user-1", "session-1", "worker-1", lease_seconds=30, now=NOW
    )
    blocked = repository.acquire_lease(
        "user-1", "session-1", "worker-2", lease_seconds=30, now=NOW
    )
    second = repository.acquire_lease(
        "user-1",
        "session-1",
        "worker-2",
        lease_seconds=30,
        now=NOW + timedelta(seconds=31),
    )

    assert first is not None
    assert blocked is None
    assert second.epoch > first.epoch


def test_renew_keeps_epoch_and_cannot_resurrect_expired_lease(tmp_path):
    repository = FileMailboxRepository(tmp_path)
    lease = repository.acquire_lease(
        "user-1", "session-1", "worker-1", lease_seconds=10, now=NOW
    )

    renewed = repository.renew_lease(
        "user-1",
        "session-1",
        lease,
        lease_seconds=10,
        now=NOW + timedelta(seconds=5),
    )
    expired = repository.renew_lease(
        "user-1",
        "session-1",
        renewed,
        lease_seconds=10,
        now=NOW + timedelta(seconds=16),
    )

    assert renewed.epoch == lease.epoch
    assert renewed.expires_at > lease.expires_at
    assert expired is None


def test_stale_owner_cannot_ack_after_lease_recovery(tmp_path):
    repository = FileMailboxRepository(tmp_path)
    repository.enqueue(event("event-1"))
    first = repository.acquire_lease(
        "user-1", "session-1", "worker-1", lease_seconds=10, now=NOW
    )
    first_claim = repository.claim_next(
        "user-1",
        "session-1",
        first,
        event_lease_seconds=10,
        now=NOW,
    )

    recovered_at = NOW + timedelta(seconds=11)
    second = repository.acquire_lease(
        "user-1", "session-1", "worker-2", lease_seconds=10, now=recovered_at
    )
    second_claim = repository.claim_next(
        "user-1",
        "session-1",
        second,
        event_lease_seconds=10,
        now=recovered_at,
    )

    assert repository.acknowledge(first_claim, first, now=recovered_at) is False
    assert repository.acknowledge(second_claim, second, now=recovered_at) is True
    assert repository.list_events("user-1", "session-1")[0].status == PROCESSED


def test_acknowledge_atomically_publishes_session_events(tmp_path):
    repository = FileMailboxRepository(tmp_path)
    repository.enqueue(event("event-1"))
    lease = repository.acquire_lease(
        "user-1", "session-1", "worker-1", lease_seconds=30, now=NOW
    )
    claimed = repository.claim_next(
        "user-1",
        "session-1",
        lease,
        event_lease_seconds=30,
        now=NOW,
    )
    projection = SessionEvent.create(
        event_id="event-1:assistant",
        event_type="assistant.turn.completed",
        session_id="session-1",
        user_id="user-1",
        origin_event_id="event-1",
        payload={"executionId": "session-1:run-1"},
        now=NOW,
    )

    assert repository.acknowledge(
        claimed,
        lease,
        session_events=[projection],
        now=NOW,
    )

    stored = repository.list_session_events("user-1", "session-1")
    assert stored == [projection]


def test_retry_then_dead_letter(tmp_path):
    repository = FileMailboxRepository(tmp_path)
    repository.enqueue(event("event-1"))

    first_lease = repository.acquire_lease(
        "user-1", "session-1", "worker-1", lease_seconds=30, now=NOW
    )
    first_claim = repository.claim_next(
        "user-1", "session-1", first_lease, event_lease_seconds=30, now=NOW
    )
    assert repository.retry(
        first_claim,
        first_lease,
        "temporary",
        delay_seconds=5,
        max_attempts=2,
        now=NOW,
    ) == PENDING
    repository.release_lease("user-1", "session-1", first_lease)

    retry_at = NOW + timedelta(seconds=6)
    second_lease = repository.acquire_lease(
        "user-1", "session-1", "worker-2", lease_seconds=30, now=retry_at
    )
    second_claim = repository.claim_next(
        "user-1",
        "session-1",
        second_lease,
        event_lease_seconds=30,
        now=retry_at,
    )
    assert repository.retry(
        second_claim,
        second_lease,
        "permanent",
        delay_seconds=5,
        max_attempts=2,
        now=retry_at,
    ) == DEAD
    assert repository.list_events("user-1", "session-1")[0].status == DEAD


def conditional_failure(operation: str = "PutItem") -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "condition failed",
            }
        },
        operation,
    )


def transaction_condition_failure() -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "TransactionCanceledException",
                "Message": "transaction cancelled",
            },
            "CancellationReasons": [
                {"Code": "ConditionalCheckFailed"},
                {"Code": "None"},
            ],
        },
        "TransactWriteItems",
    )


def test_dynamodb_enqueue_uses_event_key_for_idempotency():
    client = MagicMock()
    repository = DynamoDBMailboxRepository("mailbox-table", client=client)
    item = event("event-1")

    assert repository.enqueue(item) is True
    transaction = client.transact_write_items.call_args.kwargs["TransactItems"]
    assert transaction[0]["ConditionCheck"]["Key"] == repository._key(
        "user-1", "session-1", "STATE"
    )
    put = transaction[1]["Put"]
    assert put["TableName"] == "mailbox-table"
    assert put["ConditionExpression"] == "attribute_not_exists(recordKey)"
    assert repository._deserialize(put["Item"])["recordKey"] == "INBOX#event-1"

    client.transact_write_items.side_effect = transaction_condition_failure()
    client.get_item.return_value = {}
    assert repository.enqueue(item) is False


def test_dynamodb_serialize_accepts_nested_decimal_values():
    repository = DynamoDBMailboxRepository("mailbox-table", client=MagicMock())

    serialized = repository._serialize({
        "artifact": {
            "wordCount": Decimal("527"),
            "confidence": Decimal("0.875"),
            "scores": [Decimal("1"), 0.5],
        },
    })

    assert repository._deserialize(serialized) == {
        "artifact": {
            "wordCount": 527,
            "confidence": 0.875,
            "scores": [1, 0.5],
        },
    }


def test_dynamodb_get_event_uses_direct_consistent_read():
    client = MagicMock()
    repository = DynamoDBMailboxRepository("mailbox-table", client=client)
    item = event("event-1")
    client.get_item.return_value = {
        "Item": repository._serialize(item.to_record()),
    }

    assert repository.get_event("user-1", "session-1", "event-1") == item
    client.get_item.assert_called_once_with(
        TableName="mailbox-table",
        Key=repository._key("user-1", "session-1", "INBOX#event-1"),
        ConsistentRead=True,
    )


def test_dynamodb_enqueue_rejects_tombstoned_session():
    client = MagicMock()
    repository = DynamoDBMailboxRepository("mailbox-table", client=client)
    client.transact_write_items.side_effect = transaction_condition_failure()
    client.get_item.return_value = {
        "Item": repository._serialize({
            "sessionKey": "USER#user-1#SESSION#session-1",
            "recordKey": "STATE",
            "deletedAt": NOW.isoformat(),
        })
    }

    with pytest.raises(SessionDeletedError):
        repository.enqueue(event("event-1"))


def test_dynamodb_claim_is_fenced_by_session_state():
    client = MagicMock()
    repository = DynamoDBMailboxRepository("mailbox-table", client=client)
    stored = event("event-1")
    client.query.return_value = {"Items": [repository._serialize(stored.to_record())]}
    lease = type("Lease", (), {"owner": "worker-1", "epoch": 4, "expires_at": 0})()

    claimed = repository.claim_next(
        "user-1",
        "session-1",
        lease,
        event_lease_seconds=30,
        now=NOW,
    )

    assert claimed.status == "processing"
    transaction = client.transact_write_items.call_args.kwargs["TransactItems"]
    state_check = transaction[0]["ConditionCheck"]
    assert repository._deserialize(state_check["Key"])["recordKey"] == "STATE"
    assert "leaseEpoch = :epoch" in state_check["ConditionExpression"]
    assert transaction[1]["Update"]["Key"] == repository._key(
        "user-1", "session-1", "INBOX#event-1"
    )


def test_dynamodb_ack_rejects_a_stale_transaction():
    client = MagicMock()
    repository = DynamoDBMailboxRepository("mailbox-table", client=client)
    client.transact_write_items.side_effect = transaction_condition_failure()
    claimed = event("event-1")
    claimed.status = "processing"
    claimed.attempts = 1
    lease = type("Lease", (), {"owner": "worker-1", "epoch": 4, "expires_at": 0})()

    assert repository.acknowledge(claimed, lease, now=NOW) is False


def test_dynamodb_ack_writes_projections_in_fenced_transaction():
    client = MagicMock()
    repository = DynamoDBMailboxRepository("mailbox-table", client=client)
    claimed = event("event-1")
    claimed.status = "processing"
    claimed.attempts = 1
    projection = SessionEvent.create(
        event_id="event-1:assistant",
        event_type="assistant.turn.completed",
        session_id="session-1",
        user_id="user-1",
        origin_event_id="event-1",
        now=NOW,
    )
    lease = type("Lease", (), {"owner": "worker-1", "epoch": 4, "expires_at": 0})()

    assert repository.acknowledge(
        claimed,
        lease,
        session_events=[projection],
        now=NOW,
    )

    transaction = client.transact_write_items.call_args.kwargs["TransactItems"]
    assert len(transaction) == 3
    stored = repository._deserialize(transaction[2]["Put"]["Item"])
    assert stored["recordKey"] == (
        "OUTBOX_V2#2026-08-06T12:00:00+00:00#event-1:assistant"
    )
    assert stored["originEventId"] == "event-1"
