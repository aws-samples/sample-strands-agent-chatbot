"""Regression tests for two research requests running at the same time.

These drive the real MetadataAwareExecutor rather than stubs, because every bug
they cover comes from state living on the shared executor instance — stubs would
reproduce the intended design, not the defect.

Concurrency is currently unreachable: the orchestrator holds one A2A stream open
per research call, so a second call cannot start until the first returns. Making
research non-blocking removes that accidental serialisation, which is why these
are pinned now rather than after the change.

Each test states the user-visible failure it prevents; the shared-state cause is
an implementation detail that a fix is free to restructure.
"""
import asyncio
import os
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Importing main constructs the default agent and the A2A server, both of which
# read configuration from the environment. Set it before the import below.
os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("ARTIFACT_BUCKET", "test-bucket")

import main  # noqa: E402
import report_manager  # noqa: E402


SESSION_A = "session-a" + "a" * 32
SESSION_B = "session-b" + "b" * 32


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    """Resolve report workspaces under tmp_path, with no cache shared between tests.

    get_report_manager memoises by session id at module scope, so without this a
    manager built by one test would hand another test a stale workspace.
    """
    monkeypatch.setattr(report_manager, "_managers", {})
    real_init = report_manager.ReportManager.__init__

    def init_under_tmp(self, session_id, user_id=None, base_dir=None):
        real_init(self, session_id, user_id, base_dir=str(tmp_path))

    monkeypatch.setattr(report_manager.ReportManager, "__init__", init_under_tmp)
    return report_manager.get_report_manager


class RecordingUpdater:
    """TaskUpdater double that records which request emitted each artifact."""

    def __init__(self, label, sink):
        self.label = label
        self.sink = sink

    async def add_artifact(self, parts, name=None):
        self.sink.append({"request": self.label, "name": name})

    async def complete(self, *args, **kwargs):
        pass

    async def update_status(self, *args, **kwargs):
        pass

    async def failed(self, *args, **kwargs):
        pass


def make_context(session_id, label, model_id):
    """A RequestContext carrying real Parts — the executor validates their type."""
    from a2a.types import Part, TextPart

    ctx = MagicMock()
    ctx.metadata = {"session_id": session_id, "user_id": "u1", "model_id": model_id}
    ctx.context_id = f"ctx-{label}"
    message = MagicMock()
    message.metadata = ctx.metadata
    message.parts = [Part(root=TextPart(kind="text", text=f"research {label}"))]
    ctx.message = message
    return ctx


def tool_streaming_agent(label, tool_count, gate=None):
    """Agent whose stream yields `tool_count` distinct tool_use events.

    Awaits between events so the two requests genuinely interleave on the loop
    instead of running to completion one after the other.
    """
    agent = MagicMock()

    async def stream_async(content_blocks, invocation_state=None, **kwargs):
        for index in range(tool_count):
            yield {
                "type": "tool_use_stream",
                "current_tool_use": {
                    "toolUseId": f"{label}-tool-{index}",
                    "name": "web_search",
                    "input": {"query": f"{label} query {index}"},
                },
            }
            if gate is not None and index == 0:
                gate.set()  # let the other request start mid-stream
            await asyncio.sleep(0)

    agent.stream_async = stream_async
    return agent


def result_emitting_agent(label, gate=None):
    """Agent that streams one tool event then finishes, so the result path runs."""
    agent = MagicMock()

    async def stream_async(content_blocks, invocation_state=None, **kwargs):
        yield {
            "type": "tool_use_stream",
            "current_tool_use": {
                "toolUseId": f"{label}-tool-0",
                "name": "web_search",
                "input": {"query": f"{label} query"},
            },
        }
        if gate is not None:
            gate.set()
        await asyncio.sleep(0)
        result = MagicMock()
        result.stop_reason = "end_turn"
        result.__str__ = lambda self, label=label: f"{label} summary"
        yield {"result": result}

    agent.stream_async = stream_async
    return agent


async def run_interleaved(executor, artifacts, tool_count=3):
    """Run two requests concurrently, B starting midway through A."""
    gate = asyncio.Event()
    agents = {
        "model-A": tool_streaming_agent("A", tool_count, gate=gate),
        "model-B": tool_streaming_agent("B", tool_count),
    }

    executor._agent_builder = lambda context_id, model_id=None: agents[model_id]

    async def request_a():
        await executor._execute_streaming(
            make_context(SESSION_A, "A", "model-A"),
            RecordingUpdater("A", artifacts),
        )

    async def request_b():
        await gate.wait()
        await executor._execute_streaming(
            make_context(SESSION_B, "B", "model-B"),
            RecordingUpdater("B", artifacts),
        )

    await asyncio.gather(request_a(), request_b())


class TestConcurrentProgressEvents:
    """Progress events must stay attributable to the request that produced them."""

    def test_neither_request_emits_a_step_number_twice(self):
        """A number repeated within one research is dropped as a duplicate.

        The orchestrator's _process_artifact keeps a `sent_research_steps` set for
        the duration of one A2A stream and skips any number already in it, so a
        repeat inside a single research silently loses that progress event.

        Across researches the same number is fine: each runs as its own A2A task
        on its own stream, with its own set.
        """
        artifacts = []
        executor = main.MetadataAwareExecutor(agent_factory=lambda context_id, model_id=None: None)

        asyncio.run(run_interleaved(executor, artifacts))

        for label in ("A", "B"):
            names = Counter(
                a["name"] for a in artifacts
                if a["request"] == label and a["name"].startswith("research_step_")
            )
            duplicated = {name: count for name, count in names.items() if count > 1}
            assert not duplicated, (
                f"request {label} reused step numbers {duplicated}, so the "
                f"orchestrator would drop those progress events"
            )

    def test_each_request_numbers_its_own_steps_from_one(self):
        """A request's steps must be numbered independently of other traffic.

        The orchestrator derives display order from the suffix, so a request
        whose first step arrives as research_step_3 renders out of order.
        """
        artifacts = []
        executor = main.MetadataAwareExecutor(agent_factory=lambda context_id, model_id=None: None)

        asyncio.run(run_interleaved(executor, artifacts))

        for label in ("A", "B"):
            steps = [
                a["name"] for a in artifacts
                if a["request"] == label and a["name"].startswith("research_step_")
            ]
            expected = [f"research_step_{i}" for i in range(1, len(steps) + 1)]
            assert steps == expected, f"request {label} produced {steps}, expected {expected}"


class TestConcurrentResultRouting:
    """A request must return its own report, never another request's."""

    def test_each_request_returns_its_own_report(self, workspaces):
        """Guards against handing the user another research's report.

        Runs both requests through the real streaming path and checks the
        research_markdown artifact each one emitted.
        """
        for session, body in ((SESSION_A, "# Report for A"), (SESSION_B, "# Report for B")):
            manager = workspaces(session, "u1")
            os.makedirs(manager.workspace, exist_ok=True)
            with open(os.path.join(manager.workspace, "research_report.md"), "w", encoding="utf-8") as handle:
                handle.write(body)

        reports = []

        class ReportCapturingUpdater(RecordingUpdater):
            async def add_artifact(self, parts, name=None):
                await super().add_artifact(parts, name)
                if name == "research_markdown":
                    reports.append({"request": self.label, "text": parts[0].root.text})

        async def scenario():
            gate = asyncio.Event()
            agents = {
                "model-A": result_emitting_agent("A", gate=gate),
                "model-B": result_emitting_agent("B"),
            }
            executor = main.MetadataAwareExecutor(
                agent_factory=lambda context_id, model_id=None: agents[model_id]
            )

            async def request_a():
                await executor._execute_streaming(
                    make_context(SESSION_A, "A", "model-A"),
                    ReportCapturingUpdater("A", []),
                )

            async def request_b():
                await gate.wait()
                await executor._execute_streaming(
                    make_context(SESSION_B, "B", "model-B"),
                    ReportCapturingUpdater("B", []),
                )

            await asyncio.gather(request_a(), request_b())

        asyncio.run(scenario())

        by_request = {r["request"]: r["text"] for r in reports}
        assert "Report for A" in by_request.get("A", ""), (
            f"request A returned {by_request.get('A')!r} — another request's research"
        )
        assert "Report for B" in by_request.get("B", ""), (
            f"request B returned {by_request.get('B')!r} — another request's research"
        )

    def test_concurrent_research_does_not_delete_the_other_report(self, workspaces):
        """Guards against one research destroying another's in-progress output.

        Two researches started from one chat must not resolve to the same report
        file, since starting a research used to clear that path.
        """
        chat_session = "chat-session" + "c" * 32

        first = workspaces(chat_session + "-task1", "u1")
        os.makedirs(first.workspace, exist_ok=True)
        first_report = os.path.join(first.workspace, "research_report.md")
        with open(first_report, "w", encoding="utf-8") as handle:
            handle.write("# Research A, still being written")

        # A second research from the same chat resolves its own report location.
        second = workspaces(chat_session + "-task2", "u1")
        second_report = os.path.join(second.workspace, "research_report.md")

        assert second_report != first_report, (
            "both researches in one chat resolve to the same report file "
            f"({first_report}), so one would destroy the other's output"
        )


class TestAgentCardBuild:
    """The card is built before any request exists."""

    def test_executor_factory_works_with_no_request_in_flight(self):
        """A LookupError here would stop the server from starting.

        The base class builds a representative agent for the AgentCard outside
        any request, so the factory must not require request state.
        """
        built = []
        executor = main.MetadataAwareExecutor(
            agent_factory=lambda context_id, model_id=None: built.append((context_id, model_id)) or "agent"
        )

        assert executor._build_context_agent("__agent_card__") == "agent"
        assert built == [("__agent_card__", None)]
