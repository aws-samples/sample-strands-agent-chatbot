import asyncio
import json
import os
import time
import logging
from typing import AsyncGenerator, Dict, Any
from .agui_event_formatter import AGUIStreamEventFormatter, extract_final_result_data
from agent.stop_signal import (
    clear_local_stop_event,
    get_stop_signal_provider,
    reset_local_stop_event,
    signal_local_stop,
)

# OpenTelemetry imports
from opentelemetry.trace import get_tracer
from opentelemetry.metrics import get_meter

from ag_ui.encoder import EventEncoder

logger = logging.getLogger(__name__)


class AGUIStreamEventProcessor:
    """Processes streaming events from the agent and formats them as AG-UI protocol events"""

    def __init__(self, thread_id: str = None, run_id: str = None):
        self.formatter = AGUIStreamEventFormatter(EventEncoder(), thread_id=thread_id, run_id=run_id)
        self.seen_tool_uses = set()
        self.current_session_id = None
        self.current_user_id = None
        self.current_run_id = run_id
        self.tool_use_registry = {}
        self._streamed_tool_calls = {}
        self._active_streamed_tool_id = None
        self.partial_response_text = ""  # Track partial response for graceful abort
        self.tool_use_started = False  # Track if tool_use has been emitted (to prevent duplicate assistant messages)

        # Code agent heartbeat tracking
        self._code_agent_active = False
        self._code_agent_start_time = None
        self._last_skill_event_time = None

        # Token usage from last completed stream (for metrics)
        self.last_usage = None

        # Last LLM call's input tokens (for context tracking - NOT accumulated)
        # This captures inputTokens from the final metadata chunk of each LLM call
        self.last_llm_input_tokens = 0

        # Stop signal provider (lazy-initialized on first check; False = resolved as unavailable)
        self._stop_signal_provider = None
        self.last_stop_check_time = 0
        self.stop_check_interval = 1.0  # Check every 1 second (configurable)
        self._stop_detected = False  # Cached stop state - once True, stops immediately
        self._cancel_called = False  # Whether agent.cancel() has already been called

        # Initialize OpenTelemetry
        self.observability_enabled = os.getenv("AGENT_OBSERVABILITY_ENABLED", "false").lower() == "true"
        self.tracer = get_tracer(__name__)
        self.meter = get_meter(__name__)

        if self.observability_enabled:
            self._init_metrics()

    def _init_metrics(self):
        """Initialize OpenTelemetry metrics for streaming"""
        self.stream_event_counter = self.meter.create_counter(
            name="stream_events_total",
            description="Total number of stream events processed",
            unit="1"
        )

        self.stream_duration = self.meter.create_histogram(
            name="stream_duration",
            description="Duration of streaming sessions",
            unit="s"
        )

        self.tool_use_counter = self.meter.create_counter(
            name="tool_uses_total",
            description="Total number of tool uses in streams",
            unit="1"
        )

        logger.debug("OpenTelemetry metrics initialized for AGUIStreamEventProcessor")

    def _get_current_timestamp(self) -> str:
        """Get current timestamp in ISO format"""
        from datetime import datetime
        return datetime.now().isoformat()

    @staticmethod
    def _extract_partial_json_string_field(
        serialized_input: str,
        field_name: str,
    ) -> str | None:
        """Read a completed string field before the surrounding JSON is complete."""
        key = json.dumps(field_name)
        key_index = serialized_input.find(key)
        if key_index < 0:
            return None

        colon_index = serialized_input.find(":", key_index + len(key))
        if colon_index < 0:
            return None

        value_start = colon_index + 1
        while (
            value_start < len(serialized_input)
            and serialized_input[value_start].isspace()
        ):
            value_start += 1

        try:
            value, _ = json.JSONDecoder().raw_decode(serialized_input[value_start:])
        except (json.JSONDecodeError, TypeError):
            return None
        return value if isinstance(value, str) and value else None

    async def _ensure_streamed_tool_call(
        self,
        tool_use_id: str,
        tool_name: str,
        *,
        emit_start: bool = True,
    ) -> str:
        """Register a tool call and emit its start event exactly once."""
        if not tool_use_id or not tool_name:
            return ""

        call = self._streamed_tool_calls.get(tool_use_id)
        if call is None:
            call = {
                "name": tool_name,
                "input": "",
                "effective_name": (
                    None if tool_name == "skill_executor" else tool_name
                ),
                "forwarded_input": "",
                "started": False,
            }
            self._streamed_tool_calls[tool_use_id] = call
            self.seen_tool_uses.add(tool_use_id)
            self.tool_use_registry[tool_use_id] = {
                "tool_name": tool_name,
                "tool_use_id": tool_use_id,
                "session_id": self.current_session_id,
                "input": {},
            }

            if self.current_session_id:
                try:
                    from utils.tool_execution_context import tool_context_manager

                    await tool_context_manager.create_context(
                        tool_use_id,
                        tool_name,
                        self.current_session_id,
                    )
                except ImportError:
                    pass
        else:
            call["name"] = tool_name

        self._active_streamed_tool_id = tool_use_id
        self.tool_use_started = True
        if not emit_start:
            return ""

        start_event = self.formatter.format_event(
            "tool_call_start",
            tool_call_id=tool_use_id,
            tool_call_name=call["effective_name"] or tool_name,
        )
        if start_event:
            call["started"] = True
        return start_event

    def _start_streamed_tool_call(
        self,
        tool_use_id: str,
        effective_name: str,
    ) -> str:
        call = self._streamed_tool_calls.get(tool_use_id)
        if call is None or call.get("started") or not effective_name:
            return ""

        call["effective_name"] = effective_name
        start_event = self.formatter.format_event(
            "tool_call_start",
            tool_call_id=tool_use_id,
            tool_call_name=effective_name,
        )
        if start_event:
            call["started"] = True
        return start_event

    def _forward_streamed_tool_input(self, tool_use_id: str) -> str:
        call = self._streamed_tool_calls.get(tool_use_id)
        if call is None or not call.get("started"):
            return ""

        serialized_input = call.get("input", "")
        forwarded_input = call.get("forwarded_input", "")
        if not serialized_input or serialized_input == forwarded_input:
            return ""

        delta = (
            serialized_input[len(forwarded_input):]
            if serialized_input.startswith(forwarded_input)
            else serialized_input
        )
        call["forwarded_input"] = serialized_input
        return self.formatter.format_event(
            "tool_call_args",
            tool_call_id=tool_use_id,
            delta=delta,
        )

    def _finish_streamed_tool_call(self, tool_use_id: str) -> str:
        """Finalize input state and emit the matching end event."""
        call = self._streamed_tool_calls.get(tool_use_id)
        if call is None:
            return ""

        serialized_input = call.get("input", "")
        try:
            parsed_input = json.loads(serialized_input) if serialized_input else {}
        except (json.JSONDecodeError, TypeError):
            parsed_input = {}

        if tool_use_id in self.tool_use_registry:
            self.tool_use_registry[tool_use_id]["input"] = parsed_input

        result = ""
        if not call.get("started"):
            effective_name = (
                parsed_input.get("tool_name")
                or parsed_input.get("script_name")
                or parsed_input.get("skill_name")
                or "skill"
            )
            result += self._start_streamed_tool_call(
                tool_use_id,
                effective_name,
            )
        result += self._forward_streamed_tool_input(tool_use_id)
        self._active_streamed_tool_id = None
        result += self.formatter.format_event(
            "tool_call_end",
            tool_call_id=tool_use_id,
        )
        return result

    @property
    def stop_signal_provider(self):
        if self._stop_signal_provider is None:
            provider = get_stop_signal_provider()
            self._stop_signal_provider = provider if provider else False
        return self._stop_signal_provider if self._stop_signal_provider is not False else None

    def _check_stop_signal(self) -> bool:
        """Check if stop has been requested for current session (throttled)."""
        if self._stop_detected:
            return True

        if not self.stop_signal_provider:
            return False

        if not self.current_user_id or not self.current_session_id or not self.current_run_id:
            return False

        current_time = time.time()
        if current_time - self.last_stop_check_time < self.stop_check_interval:
            return False
        self.last_stop_check_time = current_time

        try:
            is_stopped = self.stop_signal_provider.is_stop_requested(
                self.current_user_id,
                self.current_session_id,
                self.current_run_id,
            )
            if is_stopped:
                self._stop_detected = True
                signal_local_stop(
                    self.current_user_id,
                    self.current_session_id,
                    self.current_run_id,
                )
                logger.info(f"[StopSignal] Stop detected for session {self.current_session_id}")
            return is_stopped
        except Exception as e:
            logger.warning(f"[StopSignal] Error: {e}")
            return False

    def _clear_stop_signal(self) -> None:
        """Clear the active run's local and DynamoDB stop state."""
        if not self.current_user_id or not self.current_session_id or not self.current_run_id:
            return

        clear_local_stop_event(
            self.current_user_id,
            self.current_session_id,
            self.current_run_id,
        )

        if self.stop_signal_provider:
            try:
                self.stop_signal_provider.clear_stop_signal(
                    self.current_user_id,
                    self.current_session_id,
                    self.current_run_id,
                )
            except Exception as e:
                logger.warning(f"[StopSignal] Error clearing stop signal: {e}")

    def _fix_cancelled_history(self, agent) -> None:
        """Replace SDK's 'Cancelled by user' placeholder with actual partial text.

        When agent.cancel() triggers, the SDK discards the in-progress assistant
        message and stores {"text": "Cancelled by user"} instead. We replace that
        with the text that was actually streamed to the frontend, or remove it
        entirely if no text was streamed yet.
        """
        if not agent.messages:
            return

        last_msg = agent.messages[-1]
        if last_msg.get("role") != "assistant":
            return

        if self.partial_response_text.strip():
            actual_text = self.partial_response_text.strip() + "\n\n**[Response interrupted by user]**"
        else:
            actual_text = "**[Response interrupted by user]**"

        # Replace text blocks only — preserve toolUse blocks if present
        content = last_msg.get("content", [])
        non_text = [block for block in content if not (isinstance(block, dict) and "text" in block)]
        last_msg["content"] = non_text + [{"text": actual_text}]
        logger.debug(f"[Cancel] Fixed history: kept {len(non_text)} non-text blocks, set text ({len(actual_text)} chars)")

    def _get_last_pending_tool_id(self) -> str:
        """Get the last tool_use_id that was started but hasn't received a result yet.

        This is used for error recovery - when an error occurs, we can emit
        an error tool_result for the pending tool so the agent can self-recover.

        Returns:
            The last pending tool_use_id, or None if no pending tools
        """
        if not self.tool_use_registry:
            return None

        # Find tools that were started but might not have completed
        # Since we track all seen tool uses, return the most recent one
        # The tool_use_registry contains {tool_use_id: {tool_name, session_id, input}}
        if self.tool_use_registry:
            # Return the last registered tool (most recent)
            last_tool_id = list(self.tool_use_registry.keys())[-1] if self.tool_use_registry else None
            if last_tool_id:
                logger.debug(f"[Error Recovery] Found pending tool: {last_tool_id}")
            return last_tool_id

        return None

    def _parse_xml_tool_calls(self, text: str) -> list:
        """Parse raw XML tool calls from Claude response"""
        import re
        import json

        tool_calls = []

        # Pattern to match <use_tools><invoke name="tool_name"><parameter name="param">value</parameter></invoke></use_tools>
        use_tools_pattern = r'<use_tools>(.*?)</use_tools>'
        invoke_pattern = r'<invoke name="([^"]+)">(.*?)</invoke>'
        parameter_pattern = r'<parameter name="([^"]+)">([^<]*)</parameter>'

        # Find all use_tools blocks
        use_tools_matches = re.findall(use_tools_pattern, text, re.DOTALL)

        for use_tools_content in use_tools_matches:
            # Find all invoke blocks within this use_tools block
            invoke_matches = re.findall(invoke_pattern, use_tools_content, re.DOTALL)

            for tool_name, parameters_content in invoke_matches:
                # Parse parameters
                parameter_matches = re.findall(parameter_pattern, parameters_content, re.DOTALL)

                # Build input dictionary
                tool_input = {}
                for param_name, param_value in parameter_matches:
                    # Try to parse as JSON if it looks like structured data
                    param_value = param_value.strip()
                    if param_value.startswith('{') or param_value.startswith('['):
                        try:
                            tool_input[param_name] = json.loads(param_value)
                        except json.JSONDecodeError:
                            tool_input[param_name] = param_value
                    else:
                        tool_input[param_name] = param_value

                # Create tool call object
                tool_call = {
                    "name": tool_name,
                    "input": tool_input
                }

                tool_calls.append(tool_call)

        return tool_calls

    def _remove_xml_tool_calls(self, text: str) -> str:
        """Remove XML tool call blocks from text, leaving any other content"""
        import re

        # Pattern to match entire <use_tools>...</use_tools> blocks
        use_tools_pattern = r'<use_tools>.*?</use_tools>'

        # Remove all use_tools blocks
        cleaned_text = re.sub(use_tools_pattern, '', text, flags=re.DOTALL)

        # Clean up extra whitespace
        cleaned_text = re.sub(r'\n\s*\n', '\n\n', cleaned_text)  # Collapse multiple newlines
        cleaned_text = cleaned_text.strip()

        return cleaned_text

    async def process_stream(self, agent, message: str, file_paths: list = None, session_id: str = None, invocation_state: dict = None, elicitation_bridge=None) -> AsyncGenerator[str, None]:
        """Process streaming events from agent with proper error handling and event separation"""

        # Store current session ID and invocation_state for tools to use
        self.current_session_id = session_id
        self.invocation_state = invocation_state or {}

        # Extract user_id from invocation_state for stop signal checking
        self.current_user_id = self.invocation_state.get('user_id')
        self.current_run_id = self.invocation_state.get('run_id', self.current_run_id)


        # Reset stop signal state for this stream
        self.last_stop_check_time = 0
        self._stop_detected = False
        self._cancel_called = False
        if self.current_user_id and self.current_session_id and self.current_run_id:
            reset_local_stop_event(
                self.current_user_id,
                self.current_session_id,
                self.current_run_id,
            )

        # Reset seen tool uses for each new stream
        self.seen_tool_uses.clear()
        self.tool_use_registry.clear()
        self._streamed_tool_calls.clear()
        self._active_streamed_tool_id = None

        # Reset partial response tracking for this stream
        self.partial_response_text = ""

        # Reset tool_use tracking flag
        self.tool_use_started = False

        # Reset last LLM input tokens for this stream
        self.last_llm_input_tokens = 0

        # Add stream-level deduplication
        # Handle both string and list (multimodal) messages
        if isinstance(message, list):
            # For list messages, create a hash based on session_id and timestamp
            import time
            stream_id = f"stream_list_{session_id or 'default'}_{int(time.time() * 1000)}"
        else:
            stream_id = f"stream_{hash(message)}_{session_id or 'default'}"

        if hasattr(self, '_active_streams'):
            if stream_id in self._active_streams:
                return
        else:
            self._active_streams = set()

        self._active_streams.add(stream_id)

        if not agent:
            yield self.formatter.format_event(
                "error",
                error_message="Agent is unavailable",
            )
            return

        # Register side-channel queue for skill executor events
        if session_id:
            from streaming import skill_event_bus
            skill_event_bus.get_or_create_queue(session_id)

        stream_iterator = None
        next_event_task = None  # Task for async generator polling (used with elicitation bridge)
        try:
            multimodal_message = self._create_multimodal_message(message, file_paths)

            # Initialize streaming
            yield self.formatter.format_event("init")

            # Pass invocation_state to agent for tool context access
            if invocation_state:
                stream_iterator = agent.stream_async(multimodal_message, invocation_state=invocation_state)
            else:
                stream_iterator = agent.stream_async(multimodal_message)

            # Documents are now fetched by frontend via S3 workspace API
            # No longer need to track documents in backend

            # Use task-based polling only when elicitation bridge is present
            # so we can emit elicitation SSE events while agent is blocked.
            # IMPORTANT: Without elicitation bridge, use direct await to preserve
            # OTel context across iterations. ensure_future() copies the current
            # context into a new Task on each call, causing OTel span tokens to be
            # created in one Task context and detached in another, which raises
            # ValueError: Token was created in a different Context.
            stream_aiter = stream_iterator.__aiter__()
            next_event_task = None

            while True:
                if elicitation_bridge:
                    # Task-based polling: check bridge for pending elicitation events
                    elicit_event = elicitation_bridge.get_pending_event_nowait()
                    if elicit_event:
                        if elicit_event["type"] == "oauth_elicitation_resolved":
                            yield self.formatter.format_event(
                                "oauth_elicitation_resolved",
                                elicitationId=elicit_event["elicitation_id"],
                                status=elicit_event["status"],
                            )
                        else:
                            yield self.formatter.format_event(
                                "oauth_elicitation",
                                authUrl=elicit_event["auth_url"],
                                message=elicit_event.get("message", ""),
                                elicitationId=elicit_event["elicitation_id"],
                            )

                    if next_event_task is None:
                        next_event_task = asyncio.ensure_future(stream_aiter.__anext__())

                    done, _ = await asyncio.wait({next_event_task}, timeout=0.1)
                    if not done:
                        # Timeout — drain skill queue and loop back to check bridge
                        async for sse in self._drain_skill_queue(session_id):
                            yield sse
                        # Check stop signal and trigger SDK cancellation (once)
                        if self._check_stop_signal() and not self._cancel_called:
                            self._cancel_called = True
                            logger.info(f"[StopSignal] Cancelling agent during elicitation polling for session {session_id}")
                            agent.cancel()
                        continue

                    try:
                        event = next_event_task.result()
                    except StopAsyncIteration:
                        break
                    finally:
                        next_event_task = None
                else:
                    # Polling mode: allows draining skill event queue in real time
                    # while skill_executor is blocking (code_agent running).
                    # Each Task is fully consumed (result/exception extracted) before
                    # a new one is created, so OTel spans are not split across Tasks.
                    if next_event_task is None:
                        next_event_task = asyncio.ensure_future(stream_aiter.__anext__())
                    done, _ = await asyncio.wait({next_event_task}, timeout=0.1)
                    if not done:
                        # Timeout — drain skill queue and loop back
                        async for sse in self._drain_skill_queue(session_id):
                            yield sse
                        # Check stop signal and trigger SDK cancellation (once)
                        if self._check_stop_signal() and not self._cancel_called:
                            self._cancel_called = True
                            logger.info(f"[StopSignal] Cancelling agent during tool execution for session {session_id}")
                            agent.cancel()
                        continue
                    try:
                        event = next_event_task.result()
                    except StopAsyncIteration:
                        break
                    finally:
                        next_event_task = None
                # Drain any skill executor side-channel events (code_step, etc.)
                async for sse in self._drain_skill_queue(session_id):
                    yield sse
                # Check stop signal periodically (throttled to reduce DB calls)
                if self._check_stop_signal() and not self._cancel_called:
                    self._cancel_called = True
                    logger.info(f"[StopSignal] Cancelling agent for session {session_id}")
                    agent.cancel()

                # Check for browser session ARN in invocation_state (for Live View)
                # This is set by A2A tool callback when browser_session_arn artifact is received
                if hasattr(self, 'invocation_state') and self.invocation_state:
                    browser_session_arn = self.invocation_state.get('browser_session_arn')
                    if browser_session_arn and not self.invocation_state.get('_browser_session_emitted'):
                        # Mark as emitted to avoid duplicate events
                        self.invocation_state['_browser_session_emitted'] = True
                        # Include browserId if available (required for session validation)
                        browser_id = self.invocation_state.get('browser_id')
                        logger.debug(f"[Live View] Emitting browser session: {browser_session_arn}")
                        metadata = {"browserSessionId": browser_session_arn}
                        if browser_id:
                            metadata["browserId"] = browser_id
                        yield self.formatter.format_event("metadata", metadata=metadata)

                # Handle final result
                if "result" in event:
                    logger.info("[Final Result] Received final result event from agent")
                    final_result = event["result"]
                    logger.info(f"[Final Result] stop_reason={getattr(final_result, 'stop_reason', 'NO_ATTR')}, has_interrupts={hasattr(final_result, 'interrupts')}")

                    # Check for interrupt (HITL - Human-in-the-loop)
                    if hasattr(final_result, 'stop_reason') and final_result.stop_reason == "interrupt":
                        if hasattr(final_result, 'interrupts') and final_result.interrupts:
                            logger.info(f"[Interrupt] Detected {len(final_result.interrupts)} interrupt(s), sending to frontend")

                            # Flush session buffer BEFORE yielding interrupt event.
                            # LocalSessionBuffer batches writes; if not flushed here, the
                            # assistant:{toolUse} message stays in memory only. When the
                            # interrupt response creates a new agent and loads the session,
                            # it would find an incomplete history and cause ValidationException.
                            session_mgr = getattr(agent, 'session_manager', None) or getattr(agent, '_session_manager', None)
                            if session_mgr and hasattr(session_mgr, 'flush'):
                                try:
                                    session_mgr.flush()
                                    logger.info("[Interrupt] Flushed session buffer before interrupt event")
                                except Exception as e:
                                    logger.error(f"[Interrupt] Failed to flush session buffer: {e}")

                            # Serialize Interrupt objects to dicts
                            interrupts_data = [
                                {
                                    "id": interrupt.id,
                                    "name": interrupt.name,
                                    "reason": interrupt.reason if hasattr(interrupt, 'reason') else None,
                                }
                                for interrupt in final_result.interrupts
                            ]

                            interrupt_event = self.formatter.format_event("interrupt", interrupts=interrupts_data)
                            logger.info("[Interrupt] Sending interrupt event to frontend")
                            yield interrupt_event
                            continue
                        else:
                            logger.warning("[Interrupt] stop_reason is interrupt but no interrupts attribute!")
                    # Handle cancellation via agent.cancel()
                    elif hasattr(final_result, 'stop_reason') and final_result.stop_reason == "cancelled":
                        logger.info(f"[Cancel] Agent cancelled for session {session_id}")
                        self._fix_cancelled_history(agent)
                        self._clear_stop_signal()
                        yield self.formatter.format_event("stop")
                        return

                    else:
                        logger.info(f"[Interrupt] Not an interrupt event (stop_reason={getattr(final_result, 'stop_reason', 'NONE')})")

                    images, result_text = extract_final_result_data(final_result)
                    logger.debug(f"[Final Result] Extracted data - has images: {bool(images)}, text length: {len(result_text) if result_text else 0}")

                    # Extract token usage from Strands SDK metrics
                    usage = None
                    try:

                        if hasattr(final_result, 'metrics') and hasattr(final_result.metrics, 'accumulated_usage'):
                            accumulated_usage = final_result.metrics.accumulated_usage

                            # accumulated_usage is a dict with camelCase keys
                            if isinstance(accumulated_usage, dict):
                                usage = {
                                    "inputTokens": accumulated_usage.get("inputTokens", 0),
                                    "outputTokens": accumulated_usage.get("outputTokens", 0),
                                    "totalTokens": accumulated_usage.get("totalTokens", 0)
                                }
                                # Add optional cache token fields if present and non-zero
                                if accumulated_usage.get("cacheReadInputTokens", 0) > 0:
                                    usage["cacheReadInputTokens"] = accumulated_usage["cacheReadInputTokens"]
                                if accumulated_usage.get("cacheWriteInputTokens", 0) > 0:
                                    usage["cacheWriteInputTokens"] = accumulated_usage["cacheWriteInputTokens"]

                                # Log detailed cache information
                                cache_read = accumulated_usage.get("cacheReadInputTokens", 0)
                                cache_write = accumulated_usage.get("cacheWriteInputTokens", 0)
                                if cache_read > 0 or cache_write > 0:
                                    logger.debug(f"[Cache Usage] Cache READ: {cache_read} tokens | Cache WRITE: {cache_write} tokens")

                                logger.debug(f"[Token Usage] Total - Input: {usage['inputTokens']}, Output: {usage['outputTokens']}, Total: {usage['totalTokens']}")

                                # Store for metrics access
                                self.last_usage = usage
                    except Exception as e:
                        logger.error(f"[Token Usage] Error extracting token usage: {e}")
                        # Continue without usage data

                    # Documents are fetched by frontend via S3 workspace API - no longer sent from backend
                    logger.debug("[Final Result] Emitting complete event and closing stream")
                    yield self.formatter.format_event("complete", message=result_text, images=images, usage=usage)
                    logger.debug("[Final Result] Complete event emitted, stream ended")

                    # Trigger compaction update using last LLM call's context size
                    session_manager = invocation_state.get("session_manager") if invocation_state else None
                    if session_manager and hasattr(session_manager, 'update_after_turn'):
                        context_size = (
                            getattr(agent.event_loop_metrics, 'latest_context_size', None)
                            or self.last_llm_input_tokens
                        )
                        if context_size:
                            try:
                                session_manager.update_after_turn(context_size, agent.agent_id, agent)
                            except Exception as e:
                                logger.warning(f"[Compaction] update_after_turn failed: {e}")

                    return


                # Handle reasoning text (separate from regular text)
                elif event.get("reasoning") and event.get("reasoningText"):
                    yield self.formatter.format_event("reasoning", reasoning_text=event["reasoningText"])

                # Handle regular text response
                elif event.get("data") and not event.get("reasoning"):
                    text_data = event["data"]

                    # Accumulate text for potential abort handling
                    self.partial_response_text += text_data

                    # Check stop signal and trigger SDK cancellation (once)
                    if self._check_stop_signal() and not self._cancel_called:
                        self._cancel_called = True
                        logger.info(f"[StopSignal] Cancelling agent during text streaming for session {session_id}")
                        agent.cancel()

                    # Check if this is a raw XML tool call that needs parsing
                    tool_calls = self._parse_xml_tool_calls(text_data)
                    if tool_calls:
                        # Process each tool call as proper tool events
                        for tool_call in tool_calls:
                            # Generate proper tool_use_id if not present
                            if not tool_call.get("toolUseId"):
                                tool_call["toolUseId"] = f"tool_{tool_call['name']}_{self._get_current_timestamp().replace(':', '').replace('-', '').replace('.', '')}"

                            # Check for duplicates
                            tool_use_id = tool_call["toolUseId"]
                            if tool_use_id and tool_use_id not in self.seen_tool_uses:
                                self.seen_tool_uses.add(tool_use_id)

                                # Register tool info with session_id
                                self.tool_use_registry[tool_use_id] = {
                                    'tool_name': tool_call["name"],
                                    'tool_use_id': tool_use_id,
                                    'session_id': self.current_session_id,
                                    'input': tool_call.get("input", {})
                                }

                                # Emit tool_use event
                                yield self.formatter.format_event("tool_use", tool_use=tool_call)
                                self.tool_use_started = True  # Mark that tool_use was emitted

                                await asyncio.sleep(0.1)

                        # Remove the XML from the text and send the remaining as regular response
                        cleaned_text = self._remove_xml_tool_calls(text_data)
                        if cleaned_text.strip():
                            yield self.formatter.format_event("response", text=cleaned_text)
                    else:
                        # Regular text response
                        yield self.formatter.format_event("response", text=text_data)
                        # Small delay to allow progress events to be processed
                        await asyncio.sleep(0.02)

                # Legacy callback wrapper. Current Strands releases emit typed
                # current_tool_use events separately, so there is nothing to relay here.
                elif event.get("callback"):
                    continue

                # Stream tool arguments as the model writes them. Strands exposes
                # both the exact delta and the cumulative serialized input.
                elif event.get("current_tool_use"):
                    tool_use = event["current_tool_use"]
                    tool_use_id = tool_use.get("toolUseId")
                    tool_name = tool_use.get("name")
                    if not tool_use_id or not tool_name:
                        continue

                    start_event = await self._ensure_streamed_tool_call(
                        tool_use_id,
                        tool_name,
                        emit_start=tool_name != "skill_executor",
                    )
                    if start_event:
                        yield start_event

                    call = self._streamed_tool_calls[tool_use_id]
                    cumulative_input = tool_use.get("input", "")
                    delta = (
                        ((event.get("delta") or {}).get("toolUse") or {}).get("input")
                    )

                    if isinstance(cumulative_input, dict):
                        cumulative_input = json.dumps(cumulative_input)
                    if not isinstance(cumulative_input, str):
                        cumulative_input = ""

                    previous_input = call.get("input", "")
                    if not isinstance(delta, str):
                        delta = (
                            cumulative_input[len(previous_input):]
                            if cumulative_input.startswith(previous_input)
                            else cumulative_input
                        )
                    call["input"] = (
                        cumulative_input
                        if cumulative_input
                        else previous_input + delta
                    )

                    if tool_name == "skill_executor" and not call.get("started"):
                        effective_name = (
                            self._extract_partial_json_string_field(
                                call["input"],
                                "tool_name",
                            )
                            or self._extract_partial_json_string_field(
                                call["input"],
                                "script_name",
                            )
                        )
                        if effective_name:
                            start_event = self._start_streamed_tool_call(
                                tool_use_id,
                                effective_name,
                            )
                            if start_event:
                                yield start_event

                    args_event = self._forward_streamed_tool_input(tool_use_id)
                    if args_event:
                        yield args_event

                # Handle tool streaming events (from async generator tools)
                elif event.get("tool_stream_event"):
                    tool_stream = event["tool_stream_event"]
                    stream_data = tool_stream.get("data", {})

                    # Check if this is browser session detected event
                    if isinstance(stream_data, dict) and stream_data.get("type") == "browser_session_detected":
                        browser_session_id = stream_data.get("browserSessionId")
                        browser_id = stream_data.get("browserId")
                        logger.debug(f"[Live View] Browser session detected: {browser_session_id}")

                        # Update invocation_state so it's available for tool result processing
                        if browser_session_id:
                            self.invocation_state['browser_session_arn'] = browser_session_id
                            if browser_id:
                                self.invocation_state['browser_id'] = browser_id

                            # Send metadata event to frontend for immediate Live View
                            metadata = {"browserSessionId": browser_session_id}
                            if browser_id:
                                metadata["browserId"] = browser_id

                            yield self.formatter.format_event("metadata", metadata=metadata)

                        # Also send a response message
                        yield self.formatter.format_event("response", text=f"\n\n*{stream_data.get('message', 'Browser session started')}*\n\n")

                    # Check if this is browser step event (real-time progress)
                    elif isinstance(stream_data, dict) and stream_data.get("type") == "browser_step":
                        step_content = stream_data.get("content", "")
                        step_number = stream_data.get("stepNumber", 0)

                        if step_content:
                            logger.debug(f"[Browser Step] Streaming browser_step_{step_number} to frontend")
                            # Send as browser_progress event (NOT response) to display in Browser Modal
                            yield self.formatter.format_event("browser_progress", content=step_content, stepNumber=step_number)

                    # Check if this is research step event (real-time progress)
                    elif isinstance(stream_data, dict) and stream_data.get("type") == "research_step":
                        step_content = stream_data.get("content", "")
                        step_number = stream_data.get("stepNumber", 0)

                        if step_content:
                            logger.debug(f"[Research Step] Step {step_number}: {step_content[:50]}...")
                            # Send as research_progress event to display in Research Agent card
                            yield self.formatter.format_event("research_progress", content=step_content, stepNumber=step_number)

                    # Check if this is a code step event (real-time tool-use progress from code agent)
                    elif isinstance(stream_data, dict) and stream_data.get("type") == "code_step":
                        step_content = stream_data.get("content", "")
                        step_number = stream_data.get("stepNumber", 0)

                        if step_content:
                            logger.debug(f"[Code Step] Step {step_number}: {step_content[:50]}...")
                            yield self.formatter.format_event("code_step", content=step_content, stepNumber=step_number)

                    # Check if this is a code todo update event
                    elif isinstance(stream_data, dict) and stream_data.get("type") == "code_todo_update":
                        todos = stream_data.get("todos", [])
                        logger.debug(f"[Code Todos] {len(todos)} todos")
                        yield self.formatter.format_event("code_todo_update", todos=todos)

                    # Check if this is code result metadata (sent after code agent completes)
                    elif isinstance(stream_data, dict) and stream_data.get("type") == "code_result_meta":
                        logger.debug(f"[Code Result Meta] files={len(stream_data.get('files_changed', []))}")
                        yield self.formatter.format_event(
                            "code_result_meta",
                            files_changed=stream_data.get("files_changed", []),
                            todos=stream_data.get("todos", []),
                            steps=stream_data.get("steps", 0),
                            status=stream_data.get("status", "completed"),
                        )

                    else:
                        # Other tool stream events (e.g., progress)
                        logger.debug(f"[Tool Stream] Received: {stream_data}")

                # Handle lifecycle events
                elif event.get("init_event_loop"):
                    # RunStartedEvent was already emitted before the stream loop;
                    # re-emitting "init" here would produce a second RunStartedEvent with
                    # a different run_id and mismatch the eventual RunFinishedEvent.
                    yield self.formatter.format_event("thinking")

                elif event.get("start_event_loop"):
                    yield self.formatter.format_event("thinking")

                # Handle ModelStreamChunkEvent with metadata (captures per-LLM-call usage)
                # This is yielded by SDK for each raw chunk from the model
                # Format: {"event": {"metadata": {"usage": {"inputTokens": N, ...}, ...}}}
                elif event.get("event") and isinstance(event.get("event"), dict):
                    raw_chunk = event["event"]
                    content_start = (
                        raw_chunk.get("contentBlockStart", {})
                        .get("start", {})
                        .get("toolUse")
                    )
                    if content_start:
                        tool_use_id = content_start.get("toolUseId")
                        tool_name = content_start.get("name")
                        if tool_use_id and tool_name:
                            start_event = await self._ensure_streamed_tool_call(
                                tool_use_id,
                                tool_name,
                                emit_start=tool_name != "skill_executor",
                            )
                            if start_event:
                                yield start_event
                    elif (
                        "contentBlockStop" in raw_chunk
                        and self._active_streamed_tool_id
                    ):
                        end_event = self._finish_streamed_tool_call(
                            self._active_streamed_tool_id
                        )
                        if end_event:
                            yield end_event
                    elif "metadata" in raw_chunk:
                        metadata = raw_chunk["metadata"]
                        usage = metadata.get("usage", {})
                        input_tokens = usage.get("inputTokens", 0)
                        if input_tokens > 0:
                            # Update last LLM input tokens (overwrite, not accumulate)
                            # Each LLM call sends metadata at the end, so this captures the most recent call
                            self.last_llm_input_tokens = input_tokens
                            logger.debug(f"[Metadata] Captured LLM inputTokens: {input_tokens:,}")

                # Handle tool results from message events
                elif event.get("message"):
                    logger.debug("[Message Event] Received message event (likely contains tool_result)")
                    async for result in self._process_message_event(event):
                        yield result

        except GeneratorExit:
            return

        except Exception as e:
            logger.error(f"Stream error: {e}")

            # Diagnostic logging for Bedrock ValidationException (toolResult/toolUse mismatch)
            err_str = str(e)
            if agent and ("ValidationException" in err_str or "toolResult" in err_str or "toolUse" in err_str
                          or "ValidationException" in type(e).__name__):
                try:
                    if hasattr(agent, 'messages') and agent.messages:
                        msg_summary = []
                        for i, msg in enumerate(agent.messages):
                            role = msg.get('role', '?')
                            content_types = []
                            for c in msg.get('content', []):
                                if isinstance(c, dict):
                                    content_types.append(list(c.keys())[0] if c else '{}')
                                else:
                                    content_types.append(str(type(c).__name__))
                            msg_summary.append(f"  [{i}] role={role} content={content_types}")
                        logger.error(f"[DIAG] agent.messages at error ({len(agent.messages)} msgs):\n" + "\n".join(msg_summary))
                    if hasattr(agent, '_interrupt_state'):
                        istate = agent._interrupt_state
                        logger.error(f"[DIAG] interrupt_state: activated={istate.activated}, interrupts={list(istate.interrupts.keys()) if hasattr(istate, 'interrupts') else '?'}")
                    if hasattr(agent, '_session_manager') and agent._session_manager:
                        sm = agent._session_manager
                        # _latest_agent_message is on base_manager for LocalSessionBuffer
                        base_sm = getattr(sm, 'base_manager', sm)
                        latest = getattr(base_sm, '_latest_agent_message', {})
                        latest_info = {k: (v.message_id if v else None) for k, v in latest.items()}
                        logger.error(f"[DIAG] session_manager._latest_agent_message ids={latest_info}")
                except Exception as diag_err:
                    logger.error(f"[DIAG] Failed to collect diagnostics: {diag_err}")

            # Check if there's a pending tool that needs an error result
            # This allows the agent to self-recover from errors
            pending_tool_id = self._get_last_pending_tool_id()
            if pending_tool_id:
                logger.debug(f"[Error Recovery] Emitting error as tool_result for {pending_tool_id}")
                error_tool_result = {
                    "toolUseId": pending_tool_id,
                    "status": "error",
                    "content": [{"text": f"Tool execution failed: {str(e)}"}]
                }
                yield self.formatter.format_event("tool_result", tool_result=error_tool_result)
            else:
                # No pending tool, emit as error event (chat message)
                yield self.formatter.format_event(
                    "error",
                    error_message="Agent processing failed",
                )

        finally:
            # Cancel any pending next-event task to avoid leaks
            if next_event_task is not None and not next_event_task.done():
                next_event_task.cancel()

            if stream_iterator and hasattr(stream_iterator, 'aclose'):
                try:
                    await stream_iterator.aclose()
                except Exception:
                    pass

            # Cleanup elicitation bridge for this session
            if elicitation_bridge and session_id:
                try:
                    from agent.mcp.elicitation_bridge import cleanup_bridge
                    cleanup_bridge(session_id)
                except Exception:
                    pass

            # Remove side-channel queue
            if session_id:
                from streaming import skill_event_bus
                skill_event_bus.remove_queue(session_id)

            if hasattr(self, '_active_streams'):
                self._active_streams.discard(stream_id)

            if self._stop_detected:
                self._clear_stop_signal()

    async def _drain_skill_queue(self, session_id):
        """Drain intermediate skill events from the side-channel queue and yield SSE."""
        if not session_id:
            return
        from streaming import skill_event_bus
        q = skill_event_bus.get_queue(session_id)
        if q is None:
            return
        while not q.empty():
            try:
                item = q.get_nowait()
            except Exception:
                break
            event_type = item.get("type")
            self._last_skill_event_time = time.time()

            if event_type == "code_agent_started":
                self._code_agent_active = True
                self._code_agent_start_time = time.time()
                yield self.formatter.format_event("code_agent_started")
            elif event_type == "code_step":
                yield self.formatter.format_event(
                    "code_step", content=item.get("content", ""), stepNumber=item.get("stepNumber", 0)
                )
            elif event_type == "code_todo_update":
                yield self.formatter.format_event("code_todo_update", todos=item.get("todos", []))
            elif event_type == "code_result_meta":
                self._code_agent_active = False
                yield self.formatter.format_event(
                    "code_result_meta",
                    files_changed=item.get("files_changed", []),
                    todos=item.get("todos", []),
                    steps=item.get("steps", 0),
                    status=item.get("status", "completed"),
                )
            elif event_type == "code_agent_heartbeat":
                yield self.formatter.format_event(
                    "code_agent_heartbeat",
                    elapsed_seconds=item.get("elapsed_seconds", 0),
                )
            elif event_type == "research_step":
                yield self.formatter.format_event(
                    "research_progress", content=item.get("content", ""), stepNumber=item.get("stepNumber", 0)
                )

        # If code agent is active and no events for 10+ seconds, emit heartbeat
        if (self._code_agent_active and self._last_skill_event_time and
                time.time() - self._last_skill_event_time >= 10):
            raw = int(time.time() - self._code_agent_start_time) if self._code_agent_start_time else 0
            elapsed = (raw // 10) * 10  # round down to 10s increments
            yield self.formatter.format_event("code_agent_heartbeat", elapsed_seconds=elapsed)
            self._last_skill_event_time = time.time()

    async def _process_message_event(self, event: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """Process message events that may contain tool results"""
        message_obj = event["message"]

        # Handle both dict and object formats
        if hasattr(message_obj, 'content'):
            content = message_obj.content
        elif isinstance(message_obj, dict) and 'content' in message_obj:
            content = message_obj['content']
        else:
            content = None

        if content:
            logger.debug(f"[Message Event] Processing message with {len(content)} content item(s)")
            for content_item in content:
                if isinstance(content_item, dict) and "toolResult" in content_item:
                    tool_result = content_item["toolResult"]
                    tool_use_id = tool_result.get("toolUseId")
                    status = tool_result.get("status", "unknown")
                    logger.debug(f"[Tool Result] Processing tool_result - toolUseId: {tool_use_id}, status: {status}")

                    # Wrap tool result processing in try-except for graceful error handling
                    # This ensures errors are returned as tool_result for agent self-recovery
                    try:
                        async for result in self._process_single_tool_result(tool_result, tool_use_id):
                            yield result
                    except Exception as e:
                        logger.error(f"[Tool Result] Error processing tool_result {tool_use_id}: {e}")
                        # Emit error as tool_result so agent can self-recover
                        error_tool_result = {
                            "toolUseId": tool_use_id or "unknown",
                            "status": "error",
                            "content": [{"text": f"Error processing tool result: {str(e)}"}]
                        }
                        yield self.formatter.format_event("tool_result", tool_result=error_tool_result)

    async def _process_single_tool_result(self, tool_result: Dict[str, Any], tool_use_id: str) -> AsyncGenerator[str, None]:
        """Process a single tool result with proper error handling"""
        # Note: browserSessionId is now handled via tool stream events (immediate)
        # No need to extract from tool result (too late)

        # Set context before tool execution and cleanup after
        if tool_use_id:
            try:
                from utils.tool_execution_context import tool_context_manager
                context = tool_context_manager.get_context(tool_use_id)
                if context:
                    # Set as current context during result processing
                    tool_context_manager.set_current_context(context)

                    # Add browser session metadata from invocation_state (for Live View)
                    self._add_browser_metadata(tool_result)

                    # Collect documents from tool result (for complete event)
                    self._collect_document_info(tool_result)

                    # Process the tool result
                    logger.debug(f"[Tool Result] Emitting tool_result event for {tool_use_id}")
                    yield self.formatter.format_event("tool_result", tool_result=tool_result)

                    # Clean up context after processing
                    tool_context_manager.clear_current_context()
                    await tool_context_manager.cleanup_context(tool_use_id)
                else:
                    # Add browser session metadata even if no context
                    self._add_browser_metadata(tool_result)

                    # Collect documents from tool result (for complete event)
                    self._collect_document_info(tool_result)

                    logger.debug(f"[Tool Result] Emitting tool_result event for {tool_use_id} (no context)")
                    yield self.formatter.format_event("tool_result", tool_result=tool_result)
            except ImportError:
                # Add browser session metadata even if import fails
                self._add_browser_metadata(tool_result)

                # Collect documents from tool result (for complete event)
                self._collect_document_info(tool_result)

                logger.debug(f"[Tool Result] Emitting tool_result event for {tool_use_id} (without tool_context_manager)")
                yield self.formatter.format_event("tool_result", tool_result=tool_result)
        else:
            # Collect documents from tool result (for complete event)
            self._collect_document_info(tool_result)

            logger.debug("[Tool Result] Emitting tool_result event (no tool_use_id)")
            yield self.formatter.format_event("tool_result", tool_result=tool_result)

    def _add_browser_metadata(self, tool_result: Dict[str, Any]) -> None:
        """Add browser session metadata to tool result if available"""
        if hasattr(self, 'invocation_state') and 'browser_session_arn' in self.invocation_state:
            if "metadata" not in tool_result:
                tool_result["metadata"] = {}
            tool_result["metadata"]["browserSessionId"] = self.invocation_state['browser_session_arn']
            if 'browser_id' in self.invocation_state:
                tool_result["metadata"]["browserId"] = self.invocation_state['browser_id']

    def _collect_document_info(self, tool_result: Dict[str, Any]) -> None:
        """Document collection is now handled by frontend via S3 workspace API.
        This method is kept as a no-op for backwards compatibility."""
        pass

    def _create_multimodal_message(self, text, file_paths: list = None):
        """Create a multimodal message with text, images, and documents for Strands SDK"""
        if isinstance(text, list):
            return text  # Already structured (e.g. HITL interrupt response)
        if not file_paths:
            return text

        # Create multimodal message format for Strands SDK
        content = []

        # Add text content
        if text.strip():
            content.append({
                "text": text
            })

        # Add file content (images and documents)
        for file_path in file_paths:
            file_data = self._encode_file_to_base64(file_path)
            if file_data:
                mime_type = self._get_file_mime_type(file_path)

                if mime_type.startswith('image/'):
                    # Handle images - Strands SDK format
                    content.append({
                        "image": {
                            "format": mime_type.split('/')[-1],  # e.g., "jpeg", "png"
                            "source": {
                                "bytes": self._base64_to_bytes(file_data)
                            }
                        }
                    })
                elif mime_type == 'application/pdf':
                    # Handle PDF documents - Strands SDK format
                    original_filename = file_path.split('/')[-1]  # Extract filename
                    # Remove extension since format is already specified as "pdf"
                    name_without_ext = original_filename.rsplit('.', 1)[0] if '.' in original_filename else original_filename
                    sanitized_filename = self._sanitize_filename_for_bedrock(name_without_ext)
                    content.append({
                        "document": {
                            "format": "pdf",
                            "name": sanitized_filename,
                            "source": {
                                "bytes": self._base64_to_bytes(file_data)
                            }
                        }
                    })

        return content if len(content) > 1 else text

    def _encode_file_to_base64(self, file_path: str) -> str:
        """Encode file to base64 string"""
        try:
            import base64
            with open(file_path, "rb") as file:
                return base64.b64encode(file.read()).decode('utf-8')
        except Exception:
            return None

    def _get_file_mime_type(self, file_path: str) -> str:
        """Get MIME type of file"""
        import mimetypes
        mime_type, _ = mimetypes.guess_type(file_path)
        return mime_type or "application/octet-stream"

    def _base64_to_bytes(self, base64_data: str) -> bytes:
        """Convert base64 string to bytes"""
        import base64
        return base64.b64decode(base64_data)

    def _sanitize_filename_for_bedrock(self, filename: str) -> str:
        """Sanitize filename for Bedrock document format:
        - Only alphanumeric characters, whitespace, hyphens, parentheses, square brackets
        - No consecutive whitespace
        - Convert underscores to hyphens
        """
        import re

        # First, replace underscores with hyphens
        sanitized = filename.replace('_', '-')

        # Keep only allowed characters: alphanumeric, whitespace, hyphens, parentheses, square brackets
        sanitized = re.sub(r'[^a-zA-Z0-9\s\-\(\)\[\]]', '', sanitized)

        # Replace multiple consecutive whitespace characters with single space
        sanitized = re.sub(r'\s+', ' ', sanitized)

        # Trim whitespace from start and end
        sanitized = sanitized.strip()

        # If name becomes empty, use default
        if not sanitized:
            sanitized = 'document'

        return sanitized
