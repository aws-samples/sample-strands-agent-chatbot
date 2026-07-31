"""Tool naming helpers shared by skill discovery and execution."""

from agent.mcp.client import canonical_mcp_tool_name


def canonical_tool_name(tool) -> str:
    """Return the stable name exposed through skill_dispatcher/skill_executor."""
    mcp_tool = getattr(tool, "mcp_tool", None)
    raw_name = getattr(mcp_tool, "name", None) or tool.tool_name
    return canonical_mcp_tool_name(raw_name)
