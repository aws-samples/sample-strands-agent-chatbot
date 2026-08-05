"""Tests for research task tracking and the /ping status it drives.

Research outlives the request that starts it (the A2A SDK keeps consuming events
after the client disconnects), so this status is what stops AgentCore Runtime
from reclaiming the container mid-report. A stuck "HealthyBusy" leaks the session
until maxLifetime; a premature "Healthy" loses the research.
"""
import asyncio
import os
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("ARTIFACT_BUCKET", "test-bucket")

import async_tasks  # noqa: E402


@pytest.fixture(autouse=True)
def clean_registry():
    async_tasks.reset()
    yield
    async_tasks.reset()


class TestPingStatus:
    def test_idle_when_nothing_is_registered(self):
        assert async_tasks.ping_status() == "Healthy"

    def test_busy_while_research_runs(self):
        async_tasks.begin("research")
        assert async_tasks.ping_status() == "HealthyBusy"

    def test_idle_again_once_research_ends(self):
        task_id = async_tasks.begin("research")
        async_tasks.end(task_id)
        assert async_tasks.ping_status() == "Healthy"

    def test_stays_busy_until_the_last_research_ends(self):
        first = async_tasks.begin("research-1")
        second = async_tasks.begin("research-2")
        async_tasks.end(first)
        assert async_tasks.ping_status() == "HealthyBusy", (
            "one research finishing marked the session idle while another ran"
        )
        async_tasks.end(second)
        assert async_tasks.ping_status() == "Healthy"

    def test_reused_id_does_not_end_another_task(self):
        first = async_tasks.begin("research-1")
        async_tasks.end(first)
        async_tasks.begin("research-2")
        assert async_tasks.end(first) is False
        assert async_tasks.ping_status() == "HealthyBusy"

    def test_unknown_id_is_reported_not_raised(self):
        assert async_tasks.end(9999) is False

    def test_concurrent_registration_from_threads(self):
        ids, ids_lock = [], threading.Lock()

        def worker():
            task_id = async_tasks.begin("threaded")
            with ids_lock:
                ids.append(task_id)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(set(ids)) == 20
        for task_id in ids:
            async_tasks.end(task_id)
        assert async_tasks.ping_status() == "Healthy"


class TestPingEndpointContract:
    """The endpoint must report the tracker, using the platform's spellings."""

    def test_reports_the_tracker_status(self):
        import main

        app = main.create_app()
        route = next(r for r in app.routes if getattr(r, "path", None) == "/ping")

        assert route.endpoint()["status"] == "Healthy"
        async_tasks.begin("research")
        assert route.endpoint()["status"] == "HealthyBusy"

    # A timestamp that advances on every ping reads as a continuous status
    # change, which stops the idle timeout from firing and exhausts the quota.
    def test_omits_time_of_last_update(self):
        import main

        app = main.create_app()
        route = next(r for r in app.routes if getattr(r, "path", None) == "/ping")
        assert "time_of_last_update" not in route.endpoint()


class TestExecutorRegistersResearch:
    """The executor has to register work, or /ping never reports it."""

    def _context(self):
        from a2a.types import Part, TextPart

        ctx = MagicMock()
        ctx.metadata = {"session_id": "s" + "1" * 32, "user_id": "u1", "model_id": "m"}
        ctx.context_id = "ctx-1"
        message = MagicMock()
        message.metadata = ctx.metadata
        message.parts = [Part(root=TextPart(kind="text", text="research this"))]
        ctx.message = message
        return ctx

    def _updater(self):
        updater = MagicMock()
        updater.add_artifact = MagicMock(side_effect=lambda *a, **k: asyncio.sleep(0))
        updater.complete = MagicMock(side_effect=lambda *a, **k: asyncio.sleep(0))
        return updater

    def test_reports_busy_while_the_agent_streams(self):
        import main

        seen = []

        agent = MagicMock()

        async def stream_async(content_blocks, invocation_state=None, **kwargs):
            seen.append(async_tasks.ping_status())
            if False:
                yield {}

        agent.stream_async = stream_async
        executor = main.MetadataAwareExecutor(agent_factory=lambda context_id, model_id=None: agent)
        asyncio.run(executor._execute_streaming(self._context(), self._updater()))

        assert seen == ["HealthyBusy"], (
            "research ran without being registered, so the platform could "
            "reclaim the container mid-report"
        )

    def test_releases_the_task_when_research_completes(self):
        import main

        agent = MagicMock()

        async def stream_async(content_blocks, invocation_state=None, **kwargs):
            if False:
                yield {}

        agent.stream_async = stream_async
        executor = main.MetadataAwareExecutor(agent_factory=lambda context_id, model_id=None: agent)
        asyncio.run(executor._execute_streaming(self._context(), self._updater()))

        assert async_tasks.ping_status() == "Healthy"

    def test_releases_the_task_when_research_fails(self):
        """A task left registered pins the session until maxLifetime."""
        import main

        agent = MagicMock()

        async def stream_async(content_blocks, invocation_state=None, **kwargs):
            raise RuntimeError("model exploded")
            yield {}

        agent.stream_async = stream_async
        executor = main.MetadataAwareExecutor(agent_factory=lambda context_id, model_id=None: agent)
        with pytest.raises(RuntimeError):
            asyncio.run(executor._execute_streaming(self._context(), self._updater()))

        assert async_tasks.ping_status() == "Healthy", (
            "a failed research stayed registered and would pin the session"
        )

    def test_releases_the_task_when_research_is_cancelled(self):
        """Stop/disconnect cancels the run; the registration must not survive it."""
        import main

        agent = MagicMock()

        async def stream_async(content_blocks, invocation_state=None, **kwargs):
            raise asyncio.CancelledError()
            yield {}

        agent.stream_async = stream_async
        executor = main.MetadataAwareExecutor(agent_factory=lambda context_id, model_id=None: agent)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(executor._execute_streaming(self._context(), self._updater()))

        assert async_tasks.ping_status() == "Healthy"
