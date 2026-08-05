"""
Session Manager Factory

Unified factory for creating session managers across all agent types.
Eliminates duplicated cloud/local mode selection logic.

Usage:
    from agent.factory import create_session_manager

    # For ChatbotAgent (text mode with buffering)
    session_manager = create_session_manager(
        session_id=session_id,
        user_id=user_id,
        mode="text",
        compaction_enabled=True,
    )

    # For VoiceAgent
    session_manager = create_session_manager(
        session_id=session_id,
        user_id=user_id,
        mode="voice",
    )

"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from strands.session.file_session_manager import FileSessionManager

from agent.config.constants import (
    DEFAULT_AWS_REGION,
    DEFAULT_COMPACTION_TOKEN_THRESHOLD,
    DEFAULT_COMPACTION_PROTECTED_TURNS,
    DEFAULT_MAX_TOOL_CONTENT_LENGTH,
    EnvVars,
)

logger = logging.getLogger(__name__)

# AgentCore Memory integration (optional, only for cloud deployment)
try:
    from bedrock_agentcore.memory.integrations.strands.config import (
        AgentCoreMemoryConfig,
        RetrievalConfig,
    )
    from bedrock_agentcore.memory.integrations.strands.session_manager import (
        AgentCoreMemorySessionManager,
    )
    AGENTCORE_MEMORY_AVAILABLE = True
except ImportError:
    AGENTCORE_MEMORY_AVAILABLE = False
    AgentCoreMemoryConfig = None
    RetrievalConfig = None
    AgentCoreMemorySessionManager = None


def is_cloud_mode() -> bool:
    """Check if running in cloud mode (AgentCore Memory available)."""
    memory_id = os.environ.get(EnvVars.MEMORY_ID)
    return memory_id is not None and AGENTCORE_MEMORY_AVAILABLE


def get_memory_id() -> Optional[str]:
    """Get Memory ID from environment."""
    return os.environ.get(EnvVars.MEMORY_ID)


def get_aws_region() -> str:
    """Get AWS region from environment or default."""
    return os.environ.get(EnvVars.AWS_REGION, DEFAULT_AWS_REGION)


def get_sessions_dir() -> Path:
    """Get the sessions directory for local file storage."""
    sessions_dir = Path(__file__).parent.parent.parent.parent / "sessions"
    sessions_dir.mkdir(exist_ok=True)
    return sessions_dir


def _threshold_for_model(model_id: Optional[str]) -> int:
    """Compaction threshold for a model, from its measured context window."""
    from agent.config.constants import compaction_threshold_for
    # agent.config, not agents.model_factory: importing anything under the
    # agents package pulls in ChatAgent and its optional native deps.
    from agent.config.model_context_windows import get_max_input_tokens

    return compaction_threshold_for(get_max_input_tokens(model_id))


@dataclass
class CompactionConfig:
    """Configuration for context compaction."""
    enabled: bool = True
    token_threshold: int = DEFAULT_COMPACTION_TOKEN_THRESHOLD
    protected_turns: int = DEFAULT_COMPACTION_PROTECTED_TURNS
    max_tool_content_length: int = DEFAULT_MAX_TOOL_CONTENT_LENGTH
    metrics_only: bool = False

    @classmethod
    def from_env(
        cls,
        enabled: bool = True,
        metrics_only: bool = False,
        model_id: Optional[str] = None,
    ) -> "CompactionConfig":
        """Create config from environment variables.

        The threshold is derived from the model's measured context window unless
        COMPACTION_TOKEN_THRESHOLD pins it explicitly. Sizing it per model matters
        because the picker spans 131k to 1.05M tokens: one fixed number either
        wastes most of a 1M window or overflows the smallest one.
        """
        return cls(
            enabled=enabled,
            token_threshold=int(os.environ.get(
                EnvVars.COMPACTION_TOKEN_THRESHOLD,
                str(_threshold_for_model(model_id))
            )),
            protected_turns=int(os.environ.get(
                EnvVars.COMPACTION_PROTECTED_TURNS,
                str(DEFAULT_COMPACTION_PROTECTED_TURNS)
            )),
            max_tool_content_length=int(os.environ.get(
                EnvVars.COMPACTION_MAX_TOOL_LENGTH,
                str(DEFAULT_MAX_TOOL_CONTENT_LENGTH)
            )),
            metrics_only=metrics_only,
        )


def create_agentcore_memory_config(
    session_id: str,
    user_id: str,
    memory_id: str,
    enable_prompt_caching: bool = True,
    retrieval_config: Optional[Dict] = None,
) -> "AgentCoreMemoryConfig":
    """
    Create AgentCoreMemoryConfig for cloud mode.

    Args:
        session_id: Session identifier
        user_id: User/actor identifier
        memory_id: AgentCore Memory ID
        enable_prompt_caching: Whether to enable prompt caching
        retrieval_config: Optional LTM retrieval configuration

    Returns:
        AgentCoreMemoryConfig instance
    """
    if not AGENTCORE_MEMORY_AVAILABLE:
        raise RuntimeError("AgentCore Memory SDK not available")

    return AgentCoreMemoryConfig(
        memory_id=memory_id,
        session_id=session_id,
        actor_id=user_id,
        enable_prompt_caching=enable_prompt_caching,
        retrieval_config=retrieval_config,
    )


def create_compacting_session_manager(
    session_id: str,
    user_id: str,
    memory_id: Optional[str] = None,
    compaction_config: Optional[CompactionConfig] = None,
    summarization_strategy_id: Optional[str] = None,
    enable_prompt_caching: bool = True,
    model_id: Optional[str] = None,
) -> Any:
    """
    Create CompactingSessionManager for cloud mode.

    Args:
        session_id: Session identifier
        user_id: User/actor identifier
        memory_id: AgentCore Memory ID (defaults to env var)
        compaction_config: Compaction settings
        summarization_strategy_id: Strategy ID for LTM summarization
        enable_prompt_caching: Whether to enable prompt caching

    Returns:
        CompactingSessionManager instance
    """
    # Lazy import to avoid circular dependencies
    from agent.session.compacting_session_manager import CompactingSessionManager

    memory_id = memory_id or get_memory_id()
    if not memory_id:
        raise ValueError("memory_id is required for CompactingSessionManager")

    aws_region = get_aws_region()
    config = compaction_config or CompactionConfig.from_env(model_id=model_id)

    agentcore_config = create_agentcore_memory_config(
        session_id=session_id,
        user_id=user_id,
        memory_id=memory_id,
        enable_prompt_caching=enable_prompt_caching,
    )

    return CompactingSessionManager(
        agentcore_memory_config=agentcore_config,
        region_name=aws_region,
        token_threshold=config.token_threshold,
        protected_turns=config.protected_turns,
        max_tool_content_length=config.max_tool_content_length,
        user_id=user_id,
        summarization_strategy_id=summarization_strategy_id,
        metrics_only=config.metrics_only,
    )


def create_local_session_manager(
    session_id: str,
    unified: bool = True,
) -> FileSessionManager:
    """
    Create local file-based session manager.

    Args:
        session_id: Session identifier
        unified: If True, use UnifiedFileSessionManager for cross-agent sharing

    Returns:
        FileSessionManager or UnifiedFileSessionManager instance
    """
    sessions_dir = get_sessions_dir()

    if unified:
        from agent.session.unified_file_session_manager import UnifiedFileSessionManager
        return UnifiedFileSessionManager(
            session_id=session_id,
            storage_dir=str(sessions_dir),
        )
    else:
        return FileSessionManager(
            session_id=session_id,
            storage_dir=str(sessions_dir),
        )


def create_session_manager(
    session_id: str,
    user_id: str,
    mode: str = "text",
    compaction_enabled: bool = True,
    summarization_strategy_id: Optional[str] = None,
    use_buffer: bool = True,
    model_id: Optional[str] = None,
) -> Any:
    """
    Create appropriate session manager based on environment and mode.

    This is the main factory function that handles cloud/local detection
    and creates the appropriate session manager.

    Args:
        session_id: Session identifier
        user_id: User/actor identifier
        mode: Agent mode - "text" or "voice"
        compaction_enabled: Whether to enable context compaction (text mode)
        summarization_strategy_id: Strategy ID for LTM summarization (text mode)
        use_buffer: Whether to wrap with LocalSessionBuffer (text mode, local only)
        model_id: Model being used, so the compaction threshold can be sized to
            its context window. Falls back to a conservative default if omitted.

    Returns:
        Configured session manager instance

    Examples:
        # For ChatbotAgent
        manager = create_session_manager(
            session_id="sess-123",
            user_id="user-456",
            mode="text",
            compaction_enabled=True,
        )

        # For VoiceAgent
        manager = create_session_manager(
            session_id="sess-123",
            user_id="user-456",
            mode="voice",
        )

    """
    memory_id = get_memory_id()

    if memory_id and AGENTCORE_MEMORY_AVAILABLE:
        # Cloud mode
        logger.debug(f"Cloud mode: Creating session manager for {mode}")
        return _create_cloud_session_manager(
            session_id=session_id,
            user_id=user_id,
            memory_id=memory_id,
            mode=mode,
            compaction_enabled=compaction_enabled,
            summarization_strategy_id=summarization_strategy_id,
            model_id=model_id,
        )
    else:
        # Local mode
        logger.debug(f"Local mode: Creating session manager for {mode}")
        return _create_local_session_manager(
            session_id=session_id,
            mode=mode,
            use_buffer=use_buffer,
        )


def _create_cloud_session_manager(
    session_id: str,
    user_id: str,
    memory_id: str,
    mode: str,
    compaction_enabled: bool,
    summarization_strategy_id: Optional[str],
    model_id: Optional[str] = None,
) -> Any:
    """Create session manager for cloud deployment."""
    from agent.session.compacting_session_manager import CompactingSessionManager

    aws_region = get_aws_region()

    agentcore_config = create_agentcore_memory_config(
        session_id=session_id,
        user_id=user_id,
        memory_id=memory_id,
        enable_prompt_caching=(mode == "text"),
    )

    if mode == "text":
        # Text mode: Full compaction support
        if compaction_enabled:
            config = CompactionConfig.from_env(enabled=True, model_id=model_id)
            return CompactingSessionManager(
                agentcore_memory_config=agentcore_config,
                region_name=aws_region,
                token_threshold=config.token_threshold,
                protected_turns=config.protected_turns,
                max_tool_content_length=config.max_tool_content_length,
                user_id=user_id,
                summarization_strategy_id=summarization_strategy_id,
            )
        else:
            # Metrics-only mode (baseline testing)
            return CompactingSessionManager(
                agentcore_memory_config=agentcore_config,
                region_name=aws_region,
                user_id=user_id,
                metrics_only=True,
            )

    elif mode == "voice":
        # Voice mode: No compaction, high thresholds
        return CompactingSessionManager(
            agentcore_memory_config=agentcore_config,
            region_name=aws_region,
            token_threshold=1_000_000,  # Effectively no compaction
            protected_turns=100,
            user_id=user_id,
            metrics_only=True,
        )

    else:
        raise ValueError(f"Unknown mode: {mode}")


def _create_local_session_manager(
    session_id: str,
    mode: str,
    use_buffer: bool,
) -> Any:
    """Create session manager for local development."""
    sessions_dir = get_sessions_dir()

    if mode == "text":
        # Text mode: UnifiedFileSessionManager with optional buffering
        from agent.session.unified_file_session_manager import UnifiedFileSessionManager

        base_manager = UnifiedFileSessionManager(
            session_id=session_id,
            storage_dir=str(sessions_dir),
        )

        if use_buffer:
            from agent.session.local_session_buffer import LocalSessionBuffer
            return LocalSessionBuffer(
                base_manager=base_manager,
                session_id=session_id,
            )
        return base_manager

    elif mode == "voice":
        # Voice mode: UnifiedFileSessionManager (no buffering)
        from agent.session.unified_file_session_manager import UnifiedFileSessionManager
        return UnifiedFileSessionManager(
            session_id=session_id,
            storage_dir=str(sessions_dir),
        )


    else:
        raise ValueError(f"Unknown mode: {mode}")
