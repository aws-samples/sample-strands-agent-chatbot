"""An agent run must be visible to /ping for its whole duration.

The run is a background task that deliberately outlives its request: the client
can disconnect and resume from the execution buffer. AgentCore Runtime decides
whether to reclaim the microVM from the /ping status, so a run it cannot see can
be killed part-way through.
"""
import asyncio

import pytest

from agent import async_tasks


@pytest.fixture(autouse=True)
def clean_registry():
    async_tasks.reset()
    yield
    async_tasks.reset()


class TestPingDuringAgentRun:
    def test_run_is_registered_and_released(self):
        """Mirrors run_agui_to_buffer: register, stream, release in finally."""
        observed = []

        async def scenario():
            task_id = async_tasks.begin("agent_run", {"execution_id": "s:r"})
            try:
                observed.append(async_tasks.ping_status())
            finally:
                async_tasks.end(task_id)
            observed.append(async_tasks.ping_status())

        asyncio.run(scenario())
        assert observed == ["HealthyBusy", "Healthy"]

    def test_two_runs_keep_the_session_alive_until_both_finish(self):
        """One run ending must not mark the container idle while another streams."""
        first = async_tasks.begin("agent_run", {"execution_id": "s:r1"})
        second = async_tasks.begin("agent_run", {"execution_id": "s:r2"})

        async_tasks.end(first)
        assert async_tasks.ping_status() == "HealthyBusy"

        async_tasks.end(second)
        assert async_tasks.ping_status() == "Healthy"


class TestChatRouterWiring:
    """The router has to be the thing that registers, not just the tracker."""

    def test_run_to_buffer_registers_the_run(self):
        """Guards the wiring: a tracker with no writer always reports Healthy."""
        import inspect

        from routers import chat

        source = inspect.getsource(chat._handle_agui_invocation)
        assert "async_tasks.begin(" in source, (
            "the agent run is never registered, so /ping reports Healthy while "
            "it streams and the platform can reclaim the container mid-run"
        )
        assert "async_tasks.end(" in source, (
            "the run is never released, so /ping reports HealthyBusy forever and "
            "pins the session until maxLifetime"
        )

    def test_release_happens_in_a_finally_block(self):
        """Errors and cancellation must not leak a registration."""
        import inspect
        import re

        from routers import chat

        source = inspect.getsource(chat._handle_agui_invocation)
        # The end() call must sit under a finally:, not only on the happy path.
        finally_body = re.search(r"\n(\s+)finally:\n(.*?)(?=\n\1\S|\Z)", source, re.DOTALL)
        assert finally_body and "async_tasks.end(" in finally_body.group(2), (
            "async_tasks.end() is not in the finally block, so a failed or "
            "cancelled run would keep the session pinned"
        )
