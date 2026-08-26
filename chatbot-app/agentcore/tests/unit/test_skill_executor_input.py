import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from skill import skill_tools


def test_skill_executor_schema_exposes_structured_tool_input():
    schema = skill_tools.skill_executor._metadata.input_model.model_json_schema()
    tool_input_schema = schema["properties"]["tool_input"]
    variants = tool_input_schema.get("anyOf", [tool_input_schema])

    assert any(variant.get("type") == "object" for variant in variants)
    assert not any(variant.get("type") == "string" for variant in variants)


def test_normalize_executor_input_accepts_legacy_json_object_string():
    assert skill_tools._normalize_executor_input(
        '{"plan": "research this"}',
        "tool_input",
    ) == {"plan": "research this"}


def test_normalize_executor_input_rejects_malformed_json():
    with pytest.raises(ValueError, match="invalid JSON"):
        skill_tools._normalize_executor_input(
            '{"plan": "research this"',
            "tool_input",
        )


def test_normalize_executor_input_rejects_non_object_json():
    with pytest.raises(ValueError, match="decode to a JSON object"):
        skill_tools._normalize_executor_input(
            '["research this"]',
            "tool_input",
        )


def test_skill_executor_does_not_dispatch_malformed_tool_input(monkeypatch):
    skill_tools._registry = MagicMock()
    execute_tool = MagicMock()
    monkeypatch.setattr(skill_tools, "_execute_tool", execute_tool)

    result = json.loads(skill_tools.skill_executor(
        tool_context=MagicMock(),
        skill_name="research-agent",
        tool_name="research_agent",
        tool_input='{"plan": "research this"',
    ))

    assert result["code"] == "INVALID_SKILL_INPUT"
    assert result["status"] == "error"
    execute_tool.assert_not_called()


def test_execute_tool_validates_required_arguments_before_calling(monkeypatch):
    class ResearchInput:
        @classmethod
        def model_validate(cls, value):
            if "plan" not in value:
                raise ValueError("plan: Field required")
            return cls()

        def model_dump(self):
            return {"plan": "research this"}

    tool_func = MagicMock(return_value="started")
    target_tool = SimpleNamespace(
        _metadata=SimpleNamespace(
            input_model=ResearchInput,
            _context_param="tool_context",
        ),
        _tool_func=tool_func,
    )

    registry = MagicMock()
    registry.get_tools.return_value = [target_tool]
    monkeypatch.setattr(skill_tools, "_registry", registry)
    monkeypatch.setattr(
        skill_tools,
        "canonical_tool_name",
        lambda _tool: "research_agent",
    )

    result = json.loads(skill_tools._execute_tool(
        tool_context=MagicMock(
            tool_use={"toolUseId": "tool-1"},
            agent=MagicMock(),
            invocation_state={},
        ),
        skill_name="research-agent",
        tool_name="research_agent",
        tool_input={},
    ))

    assert result["code"] == "INVALID_TOOL_INPUT"
    assert "plan" in result["error"]
    tool_func.assert_not_called()


def test_execute_tool_dispatches_validated_input(monkeypatch):
    class ResearchInput:
        def __init__(self, plan):
            self.plan = plan

        @classmethod
        def model_validate(cls, value):
            if "plan" not in value:
                raise ValueError("plan: Field required")
            return cls(plan=value["plan"])

        def model_dump(self):
            return {"plan": self.plan}

    tool_func = MagicMock(return_value="started")
    target_tool = SimpleNamespace(
        _metadata=SimpleNamespace(
            input_model=ResearchInput,
            _context_param="tool_context",
        ),
        _tool_func=tool_func,
    )

    registry = MagicMock()
    registry.get_tools.return_value = [target_tool]
    monkeypatch.setattr(skill_tools, "_registry", registry)
    monkeypatch.setattr(
        skill_tools,
        "canonical_tool_name",
        lambda _tool: "research_agent",
    )

    context = MagicMock(
        tool_use={"toolUseId": "tool-1"},
        agent=MagicMock(),
        invocation_state={},
    )
    result = skill_tools._execute_tool(
        tool_context=context,
        skill_name="research-agent",
        tool_name="research_agent",
        tool_input={"plan": "research this"},
    )

    assert result == "started"
    tool_func.assert_called_once()
    call_kwargs = tool_func.call_args.kwargs
    assert call_kwargs["plan"] == "research this"
    assert call_kwargs["tool_context"].tool_use == context.tool_use
