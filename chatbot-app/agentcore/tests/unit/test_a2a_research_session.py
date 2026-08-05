"""The research agent's session id must be scoped per research call.

The research agent derives its report workspace and chart S3 prefix from the
session id it is handed. Passing the chat's session id gave every research in a
conversation the same workspace, so a second research overwrote the first's
research_report.md. See the research agent's tests/test_concurrent_research.py
for the other half of this.
"""
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def research_tool(monkeypatch):
    """Build the research A2A tool with its registry and transport stubbed out.

    Returns (tool_impl, sent) where `sent` records the args of each A2A call.
    """
    import a2a_tools

    skill = MagicMock()
    skill.description = "Research agent"
    registry_client = MagicMock()
    registry_client.get_a2a_skill.return_value = skill
    registry_module = MagicMock()
    registry_module.get_registry_client.return_value = registry_client
    monkeypatch.setitem(sys.modules, "registry.client", registry_module)

    monkeypatch.setattr(a2a_tools, "get_cached_agent_url", lambda agent_id: "http://research.test/")

    sent = []

    async def fake_send(agent_id, message, session_id=None, region=None, metadata=None, auth_token=None):
        sent.append({
            "session_id": session_id,
            "metadata": metadata or {},
            "message": message,
        })
        yield {"status": "success", "content": [{"text": "# Findings"}]}

    monkeypatch.setattr(a2a_tools, "send_a2a_message", fake_send)

    tool = a2a_tools.create_a2a_tool("agentcore_research-agent")
    assert tool is not None
    return tool._tool_func, sent


def make_tool_context(tool_use_id, session_id="chat-session-1"):
    context = MagicMock()
    context.tool_use = {"toolUseId": tool_use_id}
    context.invocation_state = {
        "session_id": session_id,
        "user_id": "u1",
        "model_id": "m1",
        "auth_token": None,
    }
    # No artifact persistence in these tests: agent.state is exercised elsewhere.
    context.agent = None
    return context


async def drain(agen):
    return [event async for event in agen]


class TestResearchSessionScoping:
    @pytest.mark.asyncio
    async def test_two_researches_in_one_chat_get_different_sessions(self, research_tool):
        """Same workspace for both would let the second delete the first's report."""
        tool_impl, sent = research_tool

        await drain(tool_impl(plan="first", tool_context=make_tool_context("tool-use-1")))
        await drain(tool_impl(plan="second", tool_context=make_tool_context("tool-use-2")))

        assert len(sent) == 2
        assert sent[0]["session_id"] != sent[1]["session_id"], (
            "both researches were sent the same session id, so they share a "
            f"report workspace ({sent[0]['session_id']})"
        )

    @pytest.mark.asyncio
    async def test_session_header_and_metadata_agree(self, research_tool):
        """The agent resolves its workspace from metadata; the runtime routes on
        the header. If they disagree, work lands in a different place than the
        session it is billed to."""
        tool_impl, sent = research_tool

        await drain(tool_impl(plan="go", tool_context=make_tool_context("tool-use-1")))

        assert sent[0]["metadata"]["session_id"] == sent[0]["session_id"]

    @pytest.mark.asyncio
    async def test_session_is_stable_for_the_same_call(self, research_tool):
        """A retry of one tool call must resume the same workspace, not orphan it."""
        tool_impl, sent = research_tool

        await drain(tool_impl(plan="go", tool_context=make_tool_context("tool-use-1")))
        await drain(tool_impl(plan="go", tool_context=make_tool_context("tool-use-1")))

        assert sent[0]["session_id"] == sent[1]["session_id"]

    @pytest.mark.asyncio
    async def test_derived_session_keeps_the_chat_session_as_a_prefix(self, research_tool):
        """Keeps researches traceable to their conversation in logs and S3."""
        tool_impl, sent = research_tool

        await drain(tool_impl(plan="go", tool_context=make_tool_context("tool-use-1", "chat-abc")))

        assert sent[0]["session_id"].startswith("chat-abc")

    @pytest.mark.asyncio
    async def test_falls_back_to_the_chat_session_without_a_tool_use_id(self, research_tool):
        """Better one shared workspace than a session id of None."""
        tool_impl, sent = research_tool

        context = make_tool_context("", "chat-abc")
        await drain(tool_impl(plan="go", tool_context=context))

        assert sent[0]["session_id"] == "chat-abc"

    @pytest.mark.asyncio
    async def test_meets_the_runtime_minimum_session_length(self, research_tool):
        """AgentCore requires >= 33 characters; send_a2a_message pads short ids,
        but the derived id should already satisfy it for real sessions."""
        tool_impl, sent = research_tool

        session = "chat-" + "s" * 28  # a realistic 33-char chat session
        await drain(tool_impl(plan="go", tool_context=make_tool_context("tool-use-1", session)))

        assert len(sent[0]["session_id"]) >= 33
