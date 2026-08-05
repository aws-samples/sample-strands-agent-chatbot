"""
ChatAgent - Text-based conversational agent
- Session management with AgentCore Memory (cloud) or File-based (local)
- AG-UI streaming via agui_event_processor
"""

import logging
import os
from typing import Dict, Any, List, Optional
from strands import Agent
from strands.tools.executors import SequentialToolExecutor
from agents.base import BaseAgent
from agents.model_factory import build_model
from agent.hooks import ResearchApprovalHook, EmailApprovalHook, GitHubApprovalHook
from agent.config.prompt_builder import (
    build_text_system_prompt,
    system_prompt_to_string,
)

# AgentCore Memory integration (optional, only for cloud deployment)
try:
    from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig  # noqa: F401
    AGENTCORE_MEMORY_AVAILABLE = True
except ImportError:
    AGENTCORE_MEMORY_AVAILABLE = False

# Import Strands built-in tools
from strands_tools.calculator import calculator

# Import local tools module (general-purpose, agent-core integrated)
import local_tools

# Import built-in tools module (AWS Bedrock-powered tools)
import builtin_tools

logger = logging.getLogger(__name__)


# Tool ID to tool object mapping
# Start with Strands built-in tools (externally managed)
TOOL_REGISTRY = {
    "calculator": calculator,
}

# Dynamically load all local tools from local_tools.__all__
# This ensures we only need to maintain the list in one place (__init__.py)
for tool_name in local_tools.__all__:
    tool_obj = getattr(local_tools, tool_name)
    TOOL_REGISTRY[tool_name] = tool_obj
    logger.debug(f"Registered local tool: {tool_name}")

# Dynamically load all builtin tools from builtin_tools.__all__
# This ensures we only need to maintain the list in one place (__init__.py)
for tool_name in builtin_tools.__all__:
    tool_obj = getattr(builtin_tools, tool_name)
    TOOL_REGISTRY[tool_name] = tool_obj
    logger.debug(f"Registered builtin tool: {tool_name}")


class ChatAgent(BaseAgent):
    """Text-based chat agent using Strands Agent with streaming"""

    def __init__(
        self,
        session_id: str,
        user_id: Optional[str] = None,
        enabled_tools: Optional[List[str]] = None,
        model_id: Optional[str] = None,
        temperature: Optional[float] = None,
        system_prompt: Optional[str] = None,
        caching_enabled: Optional[bool] = None,
        compaction_enabled: Optional[bool] = None,
        use_null_conversation_manager: Optional[bool] = None,
        agent_id: Optional[str] = None,
        auth_token: Optional[str] = None,
        allow_user_federation: bool = True,
        concise_mode: bool = False,
    ):
        self.agent = None
        self.use_null_conversation_manager = use_null_conversation_manager if use_null_conversation_manager is not None else False
        self.auth_token = auth_token

        super().__init__(
            session_id=session_id,
            user_id=user_id,
            enabled_tools=enabled_tools,
            model_id=model_id,
            temperature=temperature,
            system_prompt=system_prompt,
            caching_enabled=caching_enabled,
            compaction_enabled=compaction_enabled,
            auth_token=auth_token,
            allow_user_federation=allow_user_federation,
            concise_mode=concise_mode,
        )

        # Create Strands agent after base initialization
        self.create_agent()

    def _build_system_prompt(self) -> Any:
        """Build text-based system prompt using prompt_builder"""
        return build_text_system_prompt(concise=getattr(self, 'concise_mode', False))

    def get_model_config(self) -> Dict[str, Any]:
        """Return model configuration"""
        return {
            "model_id": self.model_id,
            "temperature": self.temperature,
            "system_prompt": system_prompt_to_string(self.system_prompt),
            "system_prompt_blocks": len(self.system_prompt),
            "caching_enabled": self.caching_enabled
        }

    def create_agent(self):
        """Create Strands agent with filtered tools and session management"""
        try:
            config = self.get_model_config()

            # Routes to BedrockModel (caching) or a Mantle OpenAI provider based
            # on model_id. Mantle models ignore caching/temperature internally.
            model = build_model(
                config["model_id"],
                temperature=config.get("temperature"),
                max_tokens=32000,
                caching_enabled=bool(self.caching_enabled),
            )

            # Create hooks
            hooks = []

            # Add research approval hook (always enabled)
            research_approval_hook = ResearchApprovalHook(app_name="chatbot")
            hooks.append(research_approval_hook)
            logger.debug("Research approval hook enabled (BeforeToolCallEvent)")

            # Add email approval hook for bulk email operations
            email_approval_hook = EmailApprovalHook(app_name="chatbot")
            hooks.append(email_approval_hook)
            logger.debug("Email approval hook enabled (BeforeToolCallEvent)")

            # Add GitHub approval hook for write operations (branch, push, PR)
            github_approval_hook = GitHubApprovalHook(app_name="chatbot")
            hooks.append(github_approval_hook)
            logger.debug("GitHub approval hook enabled (BeforeToolCallEvent)")

            # Create agent with session manager, hooks, and system prompt as list of content blocks
            agent_kwargs = {
                "model": model,
                "system_prompt": self.system_prompt,  # List[SystemContentBlock]
                "tools": self.tools,
                "session_manager": self.session_manager,
                "hooks": hooks if hooks else None,
                "agent_id": "default"  # Fixed agent_id for state persistence across requests
            }

            # Use SequentialToolExecutor when artifact-saving tools are enabled
            # This prevents race conditions when multiple tools try to save to agent.state.artifacts
            ARTIFACT_SAVING_TOOLS = {
                'create_word_document', 'modify_word_document',
                'create_excel_spreadsheet', 'modify_excel_spreadsheet',
                'create_presentation', 'update_slide_content', 'add_slide',
                'delete_slides', 'move_slide', 'duplicate_slide', 'update_slide_notes'
            }
            # Get tool names from _tool_name attribute (set by @tool decorator)
            enabled_tool_names = set()
            for tool in self.tools:
                tool_name = None
                if hasattr(tool, '_tool_name'):
                    tool_name = tool._tool_name
                elif hasattr(tool, '__name__'):
                    tool_name = tool.__name__
                if tool_name:
                    enabled_tool_names.add(tool_name)
                logger.debug(f"[ToolExecutor] Tool: {tool}, _tool_name={getattr(tool, '_tool_name', 'N/A')}, __name__={getattr(tool, '__name__', 'N/A')}")

            logger.info(f"[ToolExecutor] Enabled tools: {enabled_tool_names}")
            logger.info(f"[ToolExecutor] Artifact-saving tools intersection: {ARTIFACT_SAVING_TOOLS & enabled_tool_names}")

            if ARTIFACT_SAVING_TOOLS & enabled_tool_names or getattr(self, '_force_sequential', False):
                agent_kwargs["tool_executor"] = SequentialToolExecutor()
                logger.info("[ToolExecutor] Using SequentialToolExecutor")
            else:
                logger.info("[ToolExecutor] Using default ConcurrentToolExecutor")

            # Use NullConversationManager if requested (disables Strands' default sliding window)
            if self.use_null_conversation_manager:
                from strands.agent.conversation_manager import NullConversationManager
                agent_kwargs["conversation_manager"] = NullConversationManager()
                logger.debug("Using NullConversationManager (no context manipulation by Strands)")

            self.agent = Agent(**agent_kwargs)

            # Calculate total characters for logging
            total_chars = sum(len(block.get("text", "")) for block in self.system_prompt)
            logger.debug(f"Agent created with {len(self.tools)} tools")
            logger.debug(f"System prompt: {len(self.system_prompt)} content blocks, {total_chars} characters")
            logger.debug(f"Session Manager: {type(self.session_manager).__name__}")

            if AGENTCORE_MEMORY_AVAILABLE and os.environ.get('MEMORY_ID'):
                logger.debug(f"   • Session: {self.session_id}, User: {self.user_id}")
                logger.debug("   • Short-term memory: Conversation history (90 days retention)")
                logger.debug("   • Long-term memory: User preferences and facts across sessions")
            else:
                logger.debug(f"   • Session: {self.session_id}")
                logger.debug(f"   • File-based persistence: {self.session_manager.storage_dir}")

        except Exception as e:
            logger.error(f"Error creating agent: {e}")
            raise
