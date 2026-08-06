import asyncio
import threading

import pytest

from agent import mailbox_runtime
from agent.mailbox import FileMailboxRepository, MailboxEvent


@pytest.fixture(autouse=True)
def clear_runtime():
    mailbox_runtime.clear_mailbox_runtime()
    yield
    mailbox_runtime.clear_mailbox_runtime()


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
