"""Tests for propagating a stop into an active A2A generator."""

import asyncio
import json

import pytest

from agent.stop_signal import clear_local_stop_event, signal_local_stop
from skill.skill_tools import _consume_async_generator


@pytest.mark.asyncio
async def test_consumer_closes_generator_when_run_is_stopped():
    started = asyncio.Event()
    finalized = asyncio.Event()

    async def blocking_generator():
        try:
            started.set()
            await asyncio.Event().wait()
            yield {"status": "success", "content": [{"text": "unreachable"}]}
        finally:
            finalized.set()

    task = asyncio.create_task(
        _consume_async_generator(
            blocking_generator(),
            user_id="user1",
            session_id="session1",
            run_id="run1",
        )
    )
    await started.wait()
    signal_local_stop("user1", "session1", "run1")

    result = json.loads(await asyncio.wait_for(task, timeout=1))

    assert result["status"] == "cancelled"
    assert finalized.is_set()
    clear_local_stop_event("user1", "session1", "run1")
