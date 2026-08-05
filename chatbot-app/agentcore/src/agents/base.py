"""
BaseAgent - Common functionality for all agent types

Provides unified interface and shared logic:
- Session management (cloud/local)
- Tool loading and filtering
- System prompt building
- Model configuration
- Gateway MCP client management
"""

import logging
from abc import ABC
from typing import List, Optional, Any

from agent.config import DEFAULT_MODEL_ID
from agent.tool_filter import filter_tools
from agent.factory import create_session_manager

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Base class for all agent types with common functionality"""

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
        auth_token: Optional[str] = None,
        allow_user_federation: bool = True,
        concise_mode: bool = False,
    ):
        self.session_id = session_id
        self.user_id = user_id or session_id
        self.enabled_tools = enabled_tools
        self.auth_token = auth_token
        self.allow_user_federation = allow_user_federation
        # Selects the concise style sections in the base prompt. Set before
        # _build_system_prompt() runs, which reads it.
        self.concise_mode = concise_mode
        self.gateway_client = None  # Gateway MCP client for lifecycle management
        self._mcp_clients = []
        self._closed = False

        # Model configuration
        self.model_id = model_id or self._get_default_model_id()
        self.temperature = temperature if temperature is not None else 0.7
        self.caching_enabled = caching_enabled if caching_enabled is not None else True
        self.compaction_enabled = compaction_enabled if compaction_enabled is not None else True

        # Load tools
        self.tools = self._load_tools()

        # Build system prompt (always build base prompt)
        self.system_prompt = self._build_system_prompt()

        # Normalize system_prompt to always be a list
        if isinstance(self.system_prompt, str):
            self.system_prompt = [{"text": self.system_prompt}]

        # Append additional system prompt if provided (e.g., artifact context)
        if system_prompt:
            self.system_prompt.append({"text": system_prompt})
            logger.debug(f"[{self.__class__.__name__}] Added additional system prompt context")

        # Create session manager
        self.session_manager = self._create_session_manager()

        logger.debug(
            f"[{self.__class__.__name__}] Initialized: "
            f"session={session_id}, user={self.user_id}, "
            f"model={self.model_id}, tools={len(self.tools)}"
        )

    def _get_default_model_id(self) -> str:
        """Get default model ID for this agent type"""
        return DEFAULT_MODEL_ID

    def _load_tools(self) -> List:
        """Load and filter tools based on enabled_tools"""
        result = filter_tools(
            enabled_tool_ids=self.enabled_tools,
            log_prefix=f"[{self.__class__.__name__}]",
            auth_token=self.auth_token,
            session_id=self.session_id,
            user_id=self.user_id,
            allow_user_federation=self.allow_user_federation,
        )

        self.gateway_client = result.clients.get("gateway")
        for client_key in ("gateway", "mcp"):
            client = result.clients.get(client_key)
            if client and client not in self._mcp_clients:
                self._mcp_clients.append(client)

        # Store elicitation bridge for SSE multiplexing
        self.elicitation_bridge = result.clients.get("elicitation_bridge")

        # Store validation errors for surfacing to user (e.g., MCP auth missing)
        self.tool_validation_errors = result.validation_errors

        return result.tools

    def _build_system_prompt(self) -> Any:
        """
        Build system prompt for this agent type

        Override this method in subclasses to customize prompt building
        """
        # Default: return empty string, subclasses should override
        return ""

    def _create_session_manager(self) -> Any:
        """
        Create session manager (cloud or local)

        Override mode parameter in subclasses:
        - "text" for ChatAgent (with compaction)
        - "voice" for VoiceAgent (no compaction)
        """
        return create_session_manager(
            session_id=self.session_id,
            user_id=self.user_id,
            mode="text",
            compaction_enabled=self.compaction_enabled,
            model_id=self.model_id,
        )


    def close(self):
        """Close all MCP clients owned by this agent."""
        if self._closed:
            return
        self._closed = True

        for client in reversed(self._mcp_clients):
            try:
                client.stop(None, None, None)
            except Exception as e:
                logger.warning(f"Failed to close MCP client: {e}")
        self._mcp_clients.clear()

    def __del__(self):
        self.close()
