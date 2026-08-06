import asyncio

import pytest

from agent.mailbox import (
    DEAD,
    PROCESSED,
    FileMailboxRepository,
    MailboxEvent,
    SessionEvent,
)
from agent.session_coordinator import SessionCoordinator


def mailbox_event(event_id: str, event_type: str = "test.ready") -> MailboxEvent:
    return MailboxEvent.create(
        event_id=event_id,
        event_type=event_type,
        session_id="session-1",
        user_id="user-1",
        source_type="test",
        source_id=event_id,
    )


@pytest.mark.asyncio
async def test_drain_processes_events_once_in_order(tmp_path):
    repository = FileMailboxRepository(tmp_path)
    repository.enqueue(mailbox_event("event-1"))
    repository.enqueue(mailbox_event("event-2"))
    observed = []

    async def handler(event):
        observed.append(event.event_id)

    result = await SessionCoordinator(
        repository,
        {"test.ready": handler},
    ).drain("user-1", "session-1", owner="worker-1")

    assert result.processed == 2
    assert observed == ["event-1", "event-2"]
    assert [event.status for event in repository.list_events("user-1", "session-1")] == [
        PROCESSED,
        PROCESSED,
    ]


@pytest.mark.asyncio
async def test_handler_projections_are_committed_with_acknowledgement(tmp_path):
    repository = FileMailboxRepository(tmp_path)
    repository.enqueue(mailbox_event("event-1"))

    async def handler(event):
        return [
            SessionEvent.create(
                event_id=f"{event.event_id}:assistant",
                event_type="assistant.turn.completed",
                session_id=event.session_id,
                user_id=event.user_id,
                origin_event_id=event.event_id,
            )
        ]

    result = await SessionCoordinator(
        repository,
        {"test.ready": handler},
    ).drain("user-1", "session-1", owner="worker-1")

    assert result.processed == 1
    assert [
        item.event_id
        for item in repository.list_session_events("user-1", "session-1")
    ] == ["event-1:assistant"]


@pytest.mark.asyncio
async def test_second_coordinator_cannot_enter_live_session(tmp_path):
    repository = FileMailboxRepository(tmp_path)
    repository.enqueue(mailbox_event("event-1"))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(event):
        entered.set()
        await release.wait()

    coordinator = SessionCoordinator(repository, {"test.ready": handler})
    first_task = asyncio.create_task(
        coordinator.drain("user-1", "session-1", owner="worker-1")
    )
    await entered.wait()

    second = await coordinator.drain(
        "user-1", "session-1", owner="worker-2"
    )
    release.set()
    first = await first_task

    assert first.processed == 1
    assert second.acquired is False


@pytest.mark.asyncio
async def test_failure_is_retried_then_dead_lettered(tmp_path):
    repository = FileMailboxRepository(tmp_path)
    repository.enqueue(mailbox_event("event-1"))

    async def handler(event):
        raise RuntimeError("failed")

    coordinator = SessionCoordinator(
        repository,
        {"test.ready": handler},
        max_attempts=2,
        retry_delay_seconds=0,
    )

    first = await coordinator.drain("user-1", "session-1", owner="worker-1")
    second = await coordinator.drain("user-1", "session-1", owner="worker-2")

    assert first.retried == 1
    assert second.dead == 1
    assert repository.list_events("user-1", "session-1")[0].status == DEAD


@pytest.mark.asyncio
async def test_unknown_event_type_is_not_retried(tmp_path):
    repository = FileMailboxRepository(tmp_path)
    repository.enqueue(mailbox_event("event-1", "unknown"))

    result = await SessionCoordinator(repository, {}).drain(
        "user-1", "session-1", owner="worker-1"
    )

    assert result.dead == 1
    assert repository.list_events("user-1", "session-1")[0].status == DEAD
