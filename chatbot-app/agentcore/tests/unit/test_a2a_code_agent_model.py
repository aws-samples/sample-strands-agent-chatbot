import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def code_tool(monkeypatch):
    import a2a_tools

    skill = MagicMock()
    skill.description = "Code agent"
    registry_client = MagicMock()
    registry_client.get_a2a_skill.return_value = skill
    registry_module = MagicMock()
    registry_module.get_registry_client.return_value = registry_client
    monkeypatch.setitem(sys.modules, "registry.client", registry_module)
    monkeypatch.setattr(
        a2a_tools,
        "get_cached_agent_url",
        lambda agent_id: "http://code.test/",
    )

    sent = []

    async def fake_send(
        agent_id,
        message,
        session_id=None,
        region=None,
        metadata=None,
        auth_token=None,
    ):
        sent.append({
            "agent_id": agent_id,
            "message": message,
            "session_id": session_id,
            "metadata": metadata or {},
        })
        yield {"status": "success", "content": [{"text": "done"}]}

    monkeypatch.setattr(a2a_tools, "send_a2a_message", fake_send)
    tool = a2a_tools.create_a2a_tool("agentcore_code-agent")
    assert tool is not None
    return tool, sent


def _context():
    context = MagicMock()
    context.tool_use = {"toolUseId": "tool-1"}
    context.invocation_state = {
        "session_id": "session-1",
        "user_id": "user-1",
        "model_id": "us.openai.gpt-5.6-terra",
        "auth_token": None,
        "workspace_paths": ["uploads/turn-context.md"],
    }
    context.agent = None
    return context


async def _drain(agen):
    return [event async for event in agen]


@pytest.mark.asyncio
async def test_code_agent_high_uses_latest_opus(code_tool):
    tool, sent = code_tool
    tool_impl = tool._tool_func
    await _drain(
        tool_impl(
            task="Implement the feature",
            workspace_paths=["inputs/spec.md"],
            task_complexity="high",
            tool_context=_context(),
        )
    )

    metadata = sent[0]["metadata"]
    assert metadata["model_id"] == "us.anthropic.claude-opus-5"
    assert metadata["task_complexity"] == "high"
    assert metadata["orchestrator_model_id"] == "us.openai.gpt-5.6-terra"
    assert metadata["workspace_paths"] == [
        "uploads/turn-context.md",
        "inputs/spec.md",
    ]


@pytest.mark.asyncio
async def test_code_agent_defaults_to_sonnet(code_tool):
    tool, sent = code_tool
    tool_impl = tool._tool_func
    await _drain(
        tool_impl(task="Fix a bug", workspace_paths=[], tool_context=_context())
    )
    assert sent[0]["metadata"]["model_id"] == "us.anthropic.claude-sonnet-5"


@pytest.mark.asyncio
async def test_code_agent_rejects_invalid_complexity(code_tool):
    tool, _sent = code_tool
    tool_impl = tool._tool_func
    with pytest.raises(ValueError, match="task_complexity"):
        await _drain(
            tool_impl(
                task="Fix a bug",
                workspace_paths=[],
                task_complexity="extreme",
                tool_context=_context(),
            )
        )


def test_code_agent_schema_exposes_complexity_enum(code_tool):
    tool, _sent = code_tool
    schema = tool._metadata.input_model.model_json_schema()
    assert schema["properties"]["task_complexity"]["enum"] == [
        "low",
        "medium",
        "high",
    ]
    assert "workspace_paths" in schema["required"]
