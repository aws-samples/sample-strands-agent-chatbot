import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent import mailbox_runtime
from agent.mailbox import FileMailboxRepository, MailboxEvent
from agent.session_coordinator import DrainResult


@pytest.fixture(autouse=True)
def clear_runtime():
    mailbox_runtime.clear_mailbox_runtime()
    yield
    mailbox_runtime.clear_mailbox_runtime()


def test_delivery_requires_mailbox_writes(monkeypatch):
    monkeypatch.setenv("SESSION_MAILBOX_DELIVERY_ENABLED", "true")
    monkeypatch.setenv("SESSION_MAILBOX_WRITE_ENABLED", "false")

    with pytest.raises(
        RuntimeError,
        match="SESSION_MAILBOX_WRITE_ENABLED",
    ):
        mailbox_runtime.validate_mailbox_configuration()


def test_write_only_rollout_is_valid(monkeypatch):
    monkeypatch.setenv("SESSION_MAILBOX_DELIVERY_ENABLED", "false")
    monkeypatch.setenv("SESSION_MAILBOX_WRITE_ENABLED", "true")

    mailbox_runtime.validate_mailbox_configuration()


@pytest.mark.asyncio
async def test_notify_drains_on_registered_runtime_loop(monkeypatch, tmp_path):
    repository = FileMailboxRepository(tmp_path)
    repository.enqueue(
        MailboxEvent.create(
            event_id="event-1",
            event_type="test.ready",
            session_id="session-1",
            user_id="user-1",
            source_type="test",
            source_id="source-1",
        )
    )
    observed = []
    target_loop = asyncio.new_event_loop()
    started = threading.Event()

    def run_loop():
        asyncio.set_event_loop(target_loop)
        started.set()
        target_loop.run_forever()

    thread = threading.Thread(target=run_loop)
    thread.start()
    started.wait(timeout=2)

    async def handler(event):
        observed.append((event.event_id, asyncio.get_running_loop()))

    monkeypatch.setattr(mailbox_runtime, "get_mailbox_repository", lambda: repository)
    mailbox_runtime.register_mailbox_runtime(
        target_loop,
        {"test.ready": handler},
    )

    try:
        result = await mailbox_runtime.notify_session_mailbox(
            "user-1",
            "session-1",
        )
    finally:
        target_loop.call_soon_threadsafe(target_loop.stop)
        thread.join(timeout=2)
        target_loop.close()

    assert result.processed == 1
    assert observed == [("event-1", target_loop)]


@pytest.mark.asyncio
async def test_notify_fails_when_runtime_is_unavailable():
    with pytest.raises(RuntimeError, match="not available"):
        await mailbox_runtime.notify_session_mailbox("user-1", "session-1")


@pytest.mark.asyncio
async def test_drain_reconciles_processed_producer_jobs(monkeypatch):
    reconciled = MagicMock(return_value=1)
    monkeypatch.setattr(
        "agent.research_jobs.reconcile_processed_deliveries",
        reconciled,
    )
    coordinator = MagicMock()
    coordinator.drain = AsyncMock(return_value=DrainResult(processed=1))
    monkeypatch.setattr(
        mailbox_runtime,
        "SessionCoordinator",
        lambda *_: coordinator,
    )
    monkeypatch.setattr(
        mailbox_runtime,
        "get_mailbox_repository",
        MagicMock(),
    )

    result = await mailbox_runtime.drain_session_mailbox(
        "user-1",
        "session-1",
        reconcile_event_ids=["event-1"],
    )

    assert result.processed == 1
    reconciled.assert_called_once_with(
        "user-1",
        "session-1",
        ["event-1"],
    )
