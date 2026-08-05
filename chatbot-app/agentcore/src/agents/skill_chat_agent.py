"""
SkillChatAgent - ChatAgent variant with progressive skill disclosure.

Inherits all of ChatAgent's functionality (streaming, session management, etc.)
but routes @skill-decorated tools through skill_dispatcher + skill_executor.
"""

import logging
import os

from agents.chat_agent import ChatAgent
from registry import (
    get_a2a_skill_tools,
    get_mcp_runtime_skills,
    get_tool_to_skill_map,
)
from skill.decorators import _apply_skill_metadata
from skill.skill_registry import SkillRegistry
from skill.skill_tools import set_dispatcher_registry
from skill.tool_names import canonical_tool_name

# Resolve skills directory relative to this file: src/agents/../../skills → agentcore/skills
_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "skills")

# Import local tools (same as ChatAgent uses)

logger = logging.getLogger(__name__)


class SkillChatAgent(ChatAgent):
    """ChatAgent with progressive skill disclosure.

    Only tools decorated with @skill are routed through skill_dispatcher/executor.
    The rest of the ChatAgent behavior (streaming, session, hooks) is inherited.
    """

    def __init__(self, *args, disabled_skills: list[str] | None = None, **kwargs):
        self._disabled_skills: set = set(disabled_skills or [])
        super().__init__(*args, **kwargs)

    def _build_system_prompt(self):
        """Build system prompt for skill-based agent.

        Skill tools get their guidance from SKILL.md (loaded on-demand).
        System prompt only includes base prompt + date.
        """
        from agent.config.prompt_builder import build_text_system_prompt

        # Delegates so the concise style swap applies here too. This is the agent
        # the chat path actually uses, so inlining BASE_TEXT_PROMPT here would
        # bypass the toggle entirely.
        return build_text_system_prompt(concise=getattr(self, 'concise_mode', False))

    def _load_tools(self):
        """Override: inject tool IDs for skills not in the disabled set."""
        if self.enabled_tools is None:
            self.enabled_tools = []
        has_auth = bool(getattr(self, 'auth_token', None))
        allow_user_federation = getattr(self, 'allow_user_federation', True)
        tool_skill_map = get_tool_to_skill_map()
        mcp_runtime_skills = get_mcp_runtime_skills()
        a2a_tools = get_a2a_skill_tools()

        def _skill_allowed(skill_name: str) -> bool:
            return skill_name not in self._disabled_skills

        for tool_name, skill_name in tool_skill_map.items():
            if not _skill_allowed(skill_name):
                continue
            if skill_name in mcp_runtime_skills:
                if not has_auth or not allow_user_federation:
                    continue
                prefixed = f"mcp_{tool_name}"
            else:
                prefixed = f"gateway_{tool_name}"
            if prefixed not in self.enabled_tools:
                self.enabled_tools.append(prefixed)

        for agent_id, skill_name in a2a_tools.items():
            if not _skill_allowed(skill_name):
                continue
            if agent_id not in self.enabled_tools:
                self.enabled_tools.append(agent_id)
                logger.debug(f"[SkillChatAgent] Auto-injected A2A skill tool: {agent_id}")

        tools = super()._load_tools()

        loaded_ids = {getattr(t, 'tool_name', None) for t in tools}
        from agents.chat_agent import TOOL_REGISTRY
        for tool_id, tool_obj in TOOL_REGISTRY.items():
            skill_name = getattr(tool_obj, '_skill_name', None)
            if skill_name and tool_id not in loaded_ids and _skill_allowed(skill_name):
                tools.append(tool_obj)
                logger.debug(f"[SkillChatAgent] Auto-loaded skill tool: {tool_id}")

        final_tools = []
        for t in tools:
            if self._is_mcp_client(t):
                mcp_skill_tools = self._extract_mcp_skill_tools(t)
                final_tools.extend(mcp_skill_tools)
                logger.info(
                    f"[SkillChatAgent] Extracted {len(mcp_skill_tools)} MCP skill tools "
                    f"from {t.__class__.__name__}"
                )
            else:
                final_tools.append(t)

        return final_tools

    @staticmethod
    def _is_mcp_client(obj) -> bool:
        """Check if an object is an MCPClient / ToolProvider (not an individual tool)."""
        # MCPClient has list_tools_sync but no tool_spec (unlike MCPAgentTool)
        return hasattr(obj, "list_tools_sync") and not hasattr(obj, "tool_spec")

    def _extract_mcp_skill_tools(self, client) -> list:
        """Start MCP client and extract individual tools with skill metadata."""
        try:
            client.start()
            paginated_tools = client.list_tools_sync()

            tool_skill_map = get_tool_to_skill_map()
            skill_tools = []
            for tool in paginated_tools:
                tool_name = canonical_tool_name(tool)
                skill_name = tool_skill_map.get(tool_name)

                if skill_name:
                    if skill_name in self._disabled_skills:
                        logger.debug(
                            f"[SkillChatAgent] MCP tool '{tool_name}' skipped "
                            f"(skill '{skill_name}' disabled)"
                        )
                        continue
                    _apply_skill_metadata(tool, skill_name)
                else:
                    logger.warning(
                        f"[SkillChatAgent] MCP tool '{tool_name}' has no skill mapping"
                    )

                skill_tools.append(tool)

            return skill_tools

        except Exception as e:
            logger.error(f"[SkillChatAgent] Failed to extract MCP tools: {e}")
            return []

    def create_agent(self):
        """Override: set up skill registry, then delegate to ChatAgent.create_agent()."""
        from skill.skill_tools import skill_dispatcher, skill_executor
        from agent.config.prompt_builder import system_prompt_to_string

        skill_tools = [t for t in self.tools if getattr(t, '_skill_name', None)]
        non_skill_tools = [t for t in self.tools if not getattr(t, '_skill_name', None)]

        if skill_tools:
            logger.info(
                f"[SkillChatAgent] Routing {len(skill_tools)} skill tools: "
                f"{[canonical_tool_name(t) for t in skill_tools]}"
            )
        if non_skill_tools:
            logger.info(
                f"[SkillChatAgent] {len(non_skill_tools)} non-skill tools passed directly: "
                f"{[getattr(t, 'tool_name', getattr(t, '__name__', str(t))) for t in non_skill_tools]}"
            )

        registry = SkillRegistry(_SKILLS_DIR)
        registry.discover_skills(exclude=self._disabled_skills)
        registry.bind_tools(skill_tools)
        set_dispatcher_registry(registry)
        self._skill_registry = registry

        catalog = registry.get_catalog()
        if self.system_prompt:
            base_prompt_text = system_prompt_to_string(self.system_prompt)
            self.system_prompt = [{"text": f"{base_prompt_text}\n\n{catalog}"}]
        else:
            self.system_prompt = [{"text": catalog}]

        self.tools = [skill_dispatcher, skill_executor] + non_skill_tools

        _SEQUENTIAL_SKILLS = {'web-search'}
        if _SEQUENTIAL_SKILLS & set(registry.skill_names):
            self._force_sequential = True
            logger.info(f"[SkillChatAgent] SequentialToolExecutor forced — skills require it: {_SEQUENTIAL_SKILLS & set(registry.skill_names)}")

        super().create_agent()

        logger.info(
            f"[SkillChatAgent] Agent created with skills: {registry.skill_names}, "
            f"tools: {list(self.agent.tool_registry.registry.keys())}"
        )
