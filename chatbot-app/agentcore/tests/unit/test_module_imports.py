"""Import checks for modules that are only imported lazily at runtime.

Modules loaded inside a function body are invisible to both ruff and the rest
of the suite, and ToolFilterRegistry swallows ImportError to degrade
gracefully. A stale import in one of them therefore surfaces only as a missing
tool in production, with a single warning in the logs. Import them here so a
broken module path fails the build instead.
"""
import importlib

import pytest

# Modules reached only through a lazy import inside a function body.
LAZILY_IMPORTED_MODULES = [
    "a2a_tools",
    "a2a_response",
    "agent.mcp.client",
    "agent.mcp.elicitation_bridge",
    "agent.stop_signal",
    "skill.tool_names",
]


@pytest.mark.parametrize("module_name", LAZILY_IMPORTED_MODULES)
def test_lazily_imported_module_is_importable(module_name):
    importlib.import_module(module_name)


def test_a2a_tool_factory_resolves():
    """ToolFilterRegistry logs and returns None on ImportError, so assert the
    factory actually resolves rather than trusting the graceful path."""
    from agent.tool_filter import ToolFilterRegistry

    assert ToolFilterRegistry()._get_a2a_tool_factory() is not None
