"""
VoiceAgent for Agent Core
- Uses Strands BidiAgent for real-time speech-to-speech interaction
- Nova Sonic model for bidirectional audio streaming
- Inherits from BaseAgent for unified architecture
- Shared tool registry with ChatAgent
- Session management integration for seamless voice-text conversation continuity
"""

import logging
import os
import sys
from typing import AsyncGenerator, Dict, Any, List, Optional

# Mock pyaudio to avoid dependency (we use browser Web Audio API, not local audio)
# This is needed because strands.experimental.bidi.io.audio imports pyaudio
# even though we don't use local audio I/O in cloud deployment
if 'pyaudio' not in sys.modules:
    import types
    fake_pyaudio = types.ModuleType('pyaudio')
    fake_pyaudio.PyAudio = type('PyAudio', (), {})
    fake_pyaudio.Stream = type('Stream', (), {})  # Required by BidiAudioIO
    fake_pyaudio.paInt16 = 8
    fake_pyaudio.paContinue = 0
    sys.modules['pyaudio'] = fake_pyaudio
from strands.experimental.bidi.agent.agent import BidiAgent
from strands.experimental.bidi.types.events import (
    BidiOutputEvent,
    BidiAudioStreamEvent,
    BidiTranscriptStreamEvent,
    BidiInterruptionEvent,
    BidiResponseCompleteEvent,
    BidiConnectionStartEvent,
    BidiConnectionCloseEvent,
    BidiErrorEvent,
)
from strands.types._events import ToolUseStreamEvent, ToolResultEvent
from strands.experimental.bidi.models.nova_sonic import BidiNovaSonicModel

# Import BaseAgent for inheritance
from agents.base import BaseAgent
# Import prompt builder for dynamic system prompt
from agent.config.prompt_builder import build_voice_system_prompt

logger = logging.getLogger(__name__)


class VoiceAgent(BaseAgent):
    """Voice-enabled agent using BidiAgent and Nova Sonic for speech-to-speech"""

    # Use separate agent_id from text mode to avoid session state conflicts
    #
    # Why separate agent_id is required:
    # - Agent (text) stores conversation_manager_state with __name__, removed_message_count, etc.
    # - BidiAgent (voice) stores conversation_manager_state = {} (empty dict)
    # - If same agent_id is used, when Agent tries to restore after BidiAgent:
    #   restore_from_session({}) raises ValueError("Invalid conversation manager state")
    #   because state.get("__name__") returns None
    #
    # Messages are stored separately per agent_id, so voice and text histories don't mix.
    # This is the intended SDK behavior for different agent types.
    VOICE_AGENT_ID = "voice"

    def __init__(
        self,
        session_id: str,
        user_id: Optional[str] = None,
        enabled_tools: Optional[List[str]] = None,
        system_prompt: Optional[str] = None,
        auth_token: Optional[str] = None,
    ):
        # Initialize base agent (handles session_id, user_id, enabled_tools, gateway_client, tools, session_manager)
        super().__init__(
            session_id=session_id,
            user_id=user_id,
            enabled_tools=enabled_tools,
            model_id=None,  # BidiAgent doesn't use model_id the same way
            system_prompt=system_prompt,
            caching_enabled=False,  # Voice mode doesn't use prompt caching
            compaction_enabled=False,  # Voice mode doesn't use compaction
            auth_token=auth_token,  # For MCP Runtime 3LO tools (Gmail, etc.)
        )

        logger.info(f"[VoiceAgent] Initialized with enabled_tools: {self.enabled_tools}")
        logger.info(f"[VoiceAgent] Filtered tools count: {len(self.tools)}")

        # Load existing conversation history from text mode (agent_id="default")
        # This enables voice mode to have context from previous text interactions
        initial_messages = self._load_text_history()

        # Initialize Nova Sonic 2 model with proper configuration
        aws_region = os.environ.get('AWS_REGION', 'us-west-2')
        model_id = os.environ.get('NOVA_SONIC_MODEL_ID', 'amazon.nova-2-sonic-v1:0')

        # Audio configuration (16kHz mono PCM - standard for Nova Sonic)
        # Voice options: matthew, tiffany, amy (default: tiffany for natural conversation)
        voice_id = os.environ.get('NOVA_SONIC_VOICE', 'tiffany')
        input_sample_rate = int(os.environ.get('NOVA_SONIC_INPUT_RATE', '16000'))
        output_sample_rate = int(os.environ.get('NOVA_SONIC_OUTPUT_RATE', '16000'))

        self.model = BidiNovaSonicModel(
            model_id=model_id,
            provider_config={
                # Audio configuration
                # https://strandsagents.com/latest/documentation/docs/api-reference/experimental/bidi/types/#strands.experimental.bidi.types.model.AudioConfig
                "audio": {
                    "voice": voice_id,
                    "input_rate": input_sample_rate,
                    "output_rate": output_sample_rate,
                    "channels": 1,  # Mono
                    "format": "pcm",  # 16-bit PCM
                },
                # Inference configuration (optional)
                # https://docs.aws.amazon.com/nova/latest/userguide/input-events.html
                "inference": {
                    # "temperature": 0.7,
                    # "top_p": 0.9,
                    # "max_tokens": 4096,
                },
            },
            client_config={
                "region": aws_region,
            },
        )

        # Create BidiAgent with session manager for conversation persistence
        # Use separate agent_id ("voice") from text mode to avoid state conflicts
        # Pass initial_messages from text mode for conversation continuity
        #
        # Note: BaseAgent normalizes system_prompt to list format [{"text": "..."}]
        # for Claude models, but BidiAgent/Nova Sonic expects a plain string.
        # Convert back to string if needed.
        voice_system_prompt = self.system_prompt
        if isinstance(voice_system_prompt, list):
            # Extract text from content blocks and join
            voice_system_prompt = "\n\n".join(
                block.get("text", "") for block in voice_system_prompt if isinstance(block, dict)
            )

        self.agent = BidiAgent(
            model=self.model,
            tools=self.tools,
            system_prompt=voice_system_prompt,
            agent_id=self.VOICE_AGENT_ID,  # "voice" - separate from text ChatbotAgent
            name="Voice Assistant",
            description="Real-time voice assistant powered by Nova Sonic",
            session_manager=self.session_manager,
            messages=initial_messages,  # Load text history for continuity
        )

        self._started = False

        logger.info(f"[VoiceAgent] Initialized with session_id={session_id}, "
                   f"session_manager={type(self.session_manager).__name__}")

    # Text agent's agent_id for loading conversation history
    TEXT_AGENT_ID = "default"

    def _get_default_model_id(self) -> Optional[str]:
        """
        Override base class method.
        BidiAgent doesn't use model_id the same way as text agents.
        """
        return None

    def _build_system_prompt(self) -> str:
        """
        Override base class method to build voice-specific system prompt.

        Returns:
            System prompt for voice mode
        """
        return build_voice_system_prompt()

    def _create_session_manager(self) -> Any:
        """
        Override base class method to use mode="voice".

        Voice mode uses unified session managers without compaction:
        - Cloud: CompactingSessionManager (metrics_only=True, no compaction)
        - Local: UnifiedFileSessionManager (reads from all agent folders)

        Returns:
            Session manager configured for voice mode
        """
        from agent.factory.session_manager_factory import create_session_manager

        return create_session_manager(
            session_id=self.session_id,
            user_id=self.user_id,
            mode="voice",
            compaction_enabled=False,
        )

    def _load_text_history(self) -> List[Dict[str, Any]]:
        """
        Load conversation history from previous interactions.

        Both cloud and local modes now use unified session managers:
        - Cloud: CompactingSessionManager (unified format under actor_id)
        - Local: UnifiedFileSessionManager (reads from all agent folders)

        Nova Sonic has a strict message limit, so we only keep the most recent
        messages for context continuity.

        Returns:
            List of messages from this session (limited to recent messages), or empty list if none found
        """
        # Nova Sonic message limit - adjust based on your needs
        # Can be configured via NOVA_SONIC_MAX_MESSAGES environment variable
        # Default: 20 messages (provides good context while staying within API limits)
        MAX_MESSAGES = int(os.environ.get('NOVA_SONIC_MAX_MESSAGES', '20'))

        try:
            if hasattr(self.session_manager, 'list_messages'):
                session_messages = self.session_manager.list_messages(
                    session_id=self.session_id,
                    agent_id=self.VOICE_AGENT_ID,
                )

                if session_messages:
                    messages = [msg.to_message() for msg in session_messages]
                    original_count = len(messages)

                    # Trim to most recent messages only
                    if original_count > MAX_MESSAGES:
                        messages = messages[-MAX_MESSAGES:]
                        logger.info(f"[VoiceAgent] Trimmed messages from {original_count} to {len(messages)} "
                                   f"(Nova Sonic limit protection)")

                    logger.info(f"[VoiceAgent] Loaded {len(messages)} messages from unified storage")
                    return messages
                else:
                    logger.debug("[VoiceAgent] No previous messages found")
                    return []
            else:
                logger.debug("[VoiceAgent] Session manager does not support list_messages")
                return []

        except Exception as e:
            logger.warning(f"[VoiceAgent] Failed to load history: {e}")
            return []

    async def start(self) -> None:
        """Start the bidirectional agent connection

        When starting, the session manager automatically loads conversation history
        from previous text/voice interactions (if any), enabling seamless continuity.
        """
        if self._started:
            logger.warning("[VoiceAgent] Already started")
            return

        invocation_state = {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "auth_token": self.auth_token,
        }

        try:
            # Log messages BEFORE start (to see what was loaded from session)
            messages_before = len(self.agent.messages)

            await self.agent.start(invocation_state=invocation_state)
            self._started = True

            # Log messages AFTER start (session manager may have loaded history)
            messages_after = len(self.agent.messages)

            if messages_after > messages_before:
                logger.info(f"[VoiceAgent] Loaded {messages_after} messages from session history "
                           f"(voice-text continuity enabled)")
            else:
                logger.info(f"[VoiceAgent] Started with {messages_after} messages (new conversation)")

        except Exception as e:
            logger.error(f"[VoiceAgent] Failed to start: {e}", exc_info=True)
            raise

    async def stop(self) -> None:
        """Stop the bidirectional agent connection"""
        try:
            if self._started:
                await self.agent.stop()
        finally:
            self._started = False
            self.close()

    async def send_audio(self, audio_base64: str, sample_rate: int = 16000) -> None:
        """Send audio chunk to the agent

        Args:
            audio_base64: Base64 encoded PCM audio
            sample_rate: Audio sample rate (default 16000 for Nova Sonic)
        """
        if not self._started:
            raise RuntimeError("Agent not started")

        try:
            await self.agent.send({
                "type": "bidi_audio_input",
                "audio": audio_base64,
                "format": "pcm",
                "sample_rate": sample_rate,
                "channels": 1,
            })
        except Exception as e:
            logger.error(f"[VoiceAgent] Error sending audio: {e}", exc_info=True)
            raise

    async def send_text(self, text: str) -> None:
        """Send text input to the agent

        Args:
            text: Text message to send
        """
        if not self._started:
            raise RuntimeError("Agent not started")

        await self.agent.send({
            "type": "bidi_text_input",
            "text": text,
            "role": "user",
        })

    async def stream_async(
        self,
        message: str = "",
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        Stream agent response (BaseAgent interface implementation).

        Note: VoiceAgent uses bidirectional streaming via WebSocket,
        not simple request-response like text agents. This method
        provides compatibility with BaseAgent interface.

        For actual voice streaming, use:
        - start() to initialize connection
        - send_audio() or send_text() to send input
        - receive_events() to get audio/transcript events
        - stop() to close connection

        Args:
            message: Text message (not used in voice mode)
            **kwargs: Additional parameters (not used in voice mode)

        Yields:
            Empty (voice mode uses receive_events() instead)
        """
        logger.warning("[VoiceAgent] stream_async() called but not implemented for voice mode. "
                      "Use start(), send_audio()/send_text(), receive_events(), stop() instead.")
        # Yield nothing - voice mode doesn't use this method
        return
        yield  # Make this a generator

    async def receive_events(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Receive and transform events from the agent for WebSocket transmission

        Yields:
            Dictionary events suitable for JSON serialization and WebSocket transmission
        """
        if not self._started:
            raise RuntimeError("Agent not started")

        try:
            async for event in self.agent.receive():
                # Transform BidiOutputEvent to dict for WebSocket
                transformed = self._transform_event(event)
                # Skip events that return None (e.g., SPECULATIVE transcripts)
                if transformed is not None:
                    yield transformed
        except Exception as e:
            error_msg = str(e)
            # Handle Nova Sonic specific errors gracefully
            if "System instability detected" in error_msg:
                logger.warning("[VoiceAgent] Nova Sonic system instability - recovering")
                yield {
                    "type": "bidi_error",
                    "message": "Voice processing interrupted. Please try again.",
                    "code": "SYSTEM_INSTABILITY",
                    "recoverable": True,
                }
            else:
                # Re-raise other exceptions
                raise

    def _transform_event(self, event: BidiOutputEvent) -> Dict[str, Any]:
        """Transform BidiOutputEvent to a JSON-serializable dict

        Args:
            event: BidiAgent output event

        Returns:
            Dictionary representation for WebSocket transmission
        """
        event_type = type(event).__name__

        # Map event types to simpler names for frontend
        if isinstance(event, BidiAudioStreamEvent):
            return {
                "type": "bidi_audio_stream",
                "audio": event.audio,
                "format": getattr(event, "format", "pcm"),
                "sample_rate": getattr(event, "sample_rate", 16000),
            }

        elif isinstance(event, BidiTranscriptStreamEvent):
            # Transcript streaming from Nova Sonic
            #
            # Nova Sonic sends transcripts in TWO stages:
            # 1. SPECULATIVE (is_final=False): Real-time preview, may change
            # 2. FINAL (is_final=True): Confirmed text, won't change
            #
            # To avoid duplicates, we ONLY forward FINAL transcripts.
            # SPECULATIVE transcripts are skipped.
            role = event.role
            is_final = getattr(event, "is_final", False)

            # event.text is the text chunk from Nova Sonic
            text = event.text or ""

            # Skip SPECULATIVE transcripts - only process FINAL
            if not is_final:
                logger.debug(f"[VoiceAgent] Skipping SPECULATIVE transcript: role={role}, "
                            f"text='{text[:50] if text else '(empty)'}...'")
                return None  # Signal to skip this event

            logger.info(f"[VoiceAgent] FINAL transcript: role={role}, text='{text[:80] if text else '(empty)'}...'")

            return {
                "type": "bidi_transcript_stream",
                "role": role,
                "delta": text,  # FINAL text - frontend accumulates
                "is_final": True,
            }

        elif isinstance(event, BidiInterruptionEvent):
            # User interrupted assistant
            logger.info("[VoiceAgent] User interrupted")
            return {
                "type": "bidi_interruption",
                "reason": getattr(event, "reason", "user_interrupt"),
            }

        elif isinstance(event, BidiResponseCompleteEvent):
            # Assistant turn complete
            logger.info("[VoiceAgent] Response complete")
            return {
                "type": "bidi_response_complete",
            }

        elif isinstance(event, BidiConnectionStartEvent):
            return {
                "type": "bidi_connection_start",
                "connection_id": getattr(event, "connection_id", self.session_id),
            }

        elif isinstance(event, BidiConnectionCloseEvent):
            return {
                "type": "bidi_connection_close",
                "reason": getattr(event, "reason", "normal"),
            }

        elif isinstance(event, BidiErrorEvent):
            return {
                "type": "bidi_error",
                "message": getattr(event, "message", "Unknown error"),
                "code": getattr(event, "code", None),
            }

        elif isinstance(event, ToolUseStreamEvent):
            # Tool use starts
            # ToolUseStreamEvent is dict-like, tool info is in current_tool_use
            current_tool = event.get("current_tool_use", {})
            tool_event = {
                "type": "tool_use",
                "toolUseId": current_tool.get("toolUseId"),
                "name": current_tool.get("name"),
                "input": current_tool.get("input", {}),
            }
            logger.info(f"[VoiceAgent] Tool use event: {tool_event}")
            return tool_event

        elif isinstance(event, ToolResultEvent):
            # ToolResultEvent is dict-like, result info is in tool_result
            tool_result = event.get("tool_result", {})
            # content can be a list of content blocks, extract text
            content = tool_result.get("content", [])
            content_text = None
            if isinstance(content, list) and len(content) > 0:
                content_text = content[0].get("text") if isinstance(content[0], dict) else str(content[0])
            elif isinstance(content, str):
                content_text = content

            result_event = {
                "type": "tool_result",
                "toolUseId": tool_result.get("toolUseId"),
                "content": content_text,
                "status": tool_result.get("status", "success"),
            }
            logger.info(f"[VoiceAgent] Tool result event: toolUseId={result_event['toolUseId']}, status={result_event['status']}")
            return result_event

        else:
            # Handle other events generically
            event_dict = {
                "type": event_type.lower().replace("event", ""),
            }

            # Copy relevant attributes
            for attr in ["toolUseId", "name", "input", "content", "status", "message"]:
                if hasattr(event, attr):
                    event_dict[attr] = getattr(event, attr)

            # Handle usage/metrics events specially (normalize to bidi_usage format)
            if "usage" in event_type.lower() or "metrics" in event_type.lower():
                event_dict["type"] = "bidi_usage"
                # Try to extract token counts from various possible attribute names
                for input_attr in ["inputTokens", "input_tokens", "promptTokens", "prompt_tokens"]:
                    if hasattr(event, input_attr):
                        event_dict["inputTokens"] = getattr(event, input_attr)
                        break
                for output_attr in ["outputTokens", "output_tokens", "completionTokens", "completion_tokens"]:
                    if hasattr(event, output_attr):
                        event_dict["outputTokens"] = getattr(event, output_attr)
                        break
                for total_attr in ["totalTokens", "total_tokens"]:
                    if hasattr(event, total_attr):
                        event_dict["totalTokens"] = getattr(event, total_attr)
                        break
                # Calculate total if not provided
                if "totalTokens" not in event_dict and "inputTokens" in event_dict and "outputTokens" in event_dict:
                    event_dict["totalTokens"] = event_dict["inputTokens"] + event_dict["outputTokens"]

            return event_dict

    async def __aenter__(self) -> "VoiceAgent":
        """Async context manager entry"""
        await self.start()
        return self

    async def __aexit__(self, *args) -> None:
        """Async context manager exit"""
        await self.stop()
