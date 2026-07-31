"""Agent module - Refactored Structure

Submodules:
    - config/: Constants, prompts, swarm configuration
    - factory/: Session manager and agent factories
    - mcp/: Native Strands MCP clients and user elicitation
    - processor/: File and multimodal content processing
    - session/: Session managers and message stores
    - hooks/: Agent lifecycle hooks

Usage:
    from agents.chat_agent import ChatAgent
    from agent.voice_agent import VoiceAgent
    from agent.stop_signal import get_stop_signal_provider

    from agent.config.constants import DEFAULT_AGENT_ID
    from agent.config.prompt_builder import build_text_system_prompt
    from agent.session.compacting_session_manager import CompactingSessionManager
    from agent.mcp.client import get_gateway_client_if_enabled
"""
