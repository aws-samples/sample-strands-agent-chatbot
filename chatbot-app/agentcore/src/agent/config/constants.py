"""
Constants and configuration values for the agent module.

Centralizes magic strings, default values, and configuration constants
used across agent, voice_agent, swarm, and session management.
"""

# =============================================================================
# Agent Identifiers
# =============================================================================

# Default agent ID for text-based ChatbotAgent
DEFAULT_AGENT_ID = "default"

# Voice agent ID - separate from text to avoid session state conflicts
# BidiAgent stores conversation_manager_state = {} (empty dict)
# Agent stores conversation_manager_state with __name__, removed_message_count
# Using same agent_id would cause restore_from_session to fail
VOICE_AGENT_ID = "voice"

# Swarm agent ID for swarm conversation storage
SWARM_AGENT_ID = "default"


# =============================================================================
# Tool Prefixes
# =============================================================================

# Gateway MCP tools prefix (e.g., "gateway_wikipedia_search")
GATEWAY_PREFIX = "gateway_"

# A2A Agent tools prefix (e.g., "agentcore_research-agent")
A2A_PREFIX = "agentcore_"


# =============================================================================
# Model Configuration
# =============================================================================

# Default text model ID
DEFAULT_MODEL_ID = "openai.gpt-5.6-terra"

# Default temperature for model inference
DEFAULT_TEMPERATURE = 0.7

# Nova Sonic model ID for voice
DEFAULT_NOVA_SONIC_MODEL_ID = "amazon.nova-2-sonic-v1:0"

# Default voice for Nova Sonic
DEFAULT_NOVA_SONIC_VOICE = "tiffany"


# =============================================================================
# AWS Configuration
# =============================================================================

# Default AWS region
DEFAULT_AWS_REGION = "us-west-2"

# Default project name for resource naming
DEFAULT_PROJECT_NAME = "strands-agent-chatbot"


# =============================================================================
# Session & Compaction Configuration
# =============================================================================

# Default token threshold for compaction (triggers checkpoint)
DEFAULT_COMPACTION_TOKEN_THRESHOLD = 100_000

# Number of recent turns to protect from truncation
DEFAULT_COMPACTION_PROTECTED_TURNS = 2

# Maximum characters for tool content before truncation
DEFAULT_MAX_TOOL_CONTENT_LENGTH = 500


# =============================================================================
# Environment Variable Names
# =============================================================================

class EnvVars:
    """Environment variable names used across the agent module."""

    # AWS
    AWS_REGION = "AWS_REGION"

    # AgentCore Memory
    MEMORY_ID = "MEMORY_ID"

    # Project
    PROJECT_NAME = "PROJECT_NAME"
    ENVIRONMENT = "ENVIRONMENT"

    # Code Interpreter
    CODE_INTERPRETER_ID = "CODE_INTERPRETER_ID"

    # Gateway
    GATEWAY_MCP_ENABLED = "GATEWAY_MCP_ENABLED"

    # Session
    SESSION_ID = "SESSION_ID"
    USER_ID = "USER_ID"

    # Compaction
    COMPACTION_TOKEN_THRESHOLD = "COMPACTION_TOKEN_THRESHOLD"
    COMPACTION_PROTECTED_TURNS = "COMPACTION_PROTECTED_TURNS"
    COMPACTION_MAX_TOOL_LENGTH = "COMPACTION_MAX_TOOL_LENGTH"

    # Nova Sonic
    NOVA_SONIC_MODEL_ID = "NOVA_SONIC_MODEL_ID"
    NOVA_SONIC_VOICE = "NOVA_SONIC_VOICE"
    NOVA_SONIC_INPUT_RATE = "NOVA_SONIC_INPUT_RATE"
    NOVA_SONIC_OUTPUT_RATE = "NOVA_SONIC_OUTPUT_RATE"


# =============================================================================
# DynamoDB Keys
# =============================================================================

# Tool registry key in DynamoDB
TOOL_REGISTRY_USER_ID = "TOOL_REGISTRY"
TOOL_REGISTRY_SK = "CONFIG"


# =============================================================================
# File Extensions
# =============================================================================

# Supported image formats
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

# Supported document formats
DOCUMENT_EXTENSIONS = (".pdf", ".csv", ".doc", ".docx", ".xls", ".xlsx", ".html", ".txt", ".md")

# Office document formats (stored to workspace)
OFFICE_EXTENSIONS = {
    "word": [".docx"],
    "excel": [".xlsx"],
    "powerpoint": [".pptx"],
}
