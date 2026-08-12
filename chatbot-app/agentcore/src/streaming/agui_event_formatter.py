import json
import base64
import copy
import uuid
import logging
from typing import Any, Dict, List, Optional, Tuple

from ag_ui.core import (
    RunStartedEvent,
    RunFinishedEvent,
    RunErrorEvent,
    RunFinishedInterruptOutcome,
    Interrupt,
    TextMessageStartEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    ToolCallStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ReasoningStartEvent,
    ReasoningMessageStartEvent,
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningEndEvent,
    CustomEvent,
    EventType,
)
from ag_ui.encoder import EventEncoder

logger = logging.getLogger(__name__)


# ================================================================== #
# Tool result parsing utilities                                        #
# (migrated from legacy event_formatter.py)                            #
# ================================================================== #

def extract_final_result_data(final_result) -> Tuple[List[Dict[str, str]], str]:
    """Extract images and text from final result."""
    images = []
    result_text = str(final_result)

    try:
        if hasattr(final_result, 'message') and hasattr(final_result.message, 'content'):
            content = final_result.message.content
            text_parts = []

            for item in content:
                if isinstance(item, dict):
                    if "text" in item:
                        text_parts.append(item["text"])
                    elif "image" in item and "source" in item["image"]:
                        image_data = item["image"]
                        images.append({
                            "format": image_data.get("format", "png"),
                            "data": image_data["source"].get("data", "")
                        })

            if text_parts:
                result_text = " ".join(text_parts)

    except Exception:
        pass

    return images, result_text


def extract_basic_content(tool_result: Dict[str, Any]) -> Tuple[str, List[Dict[str, str]]]:
    """Extract basic text and image content from MCP format."""
    result_text = ""
    result_images = []

    # Handle case where content might be a JSON string
    if "content" in tool_result and isinstance(tool_result["content"], str):
        try:
            parsed_content = json.loads(tool_result["content"])
            tool_result = tool_result.copy()
            tool_result["content"] = parsed_content
        except json.JSONDecodeError:
            pass

    if "content" in tool_result:
        content = tool_result["content"]

        for idx, item in enumerate(content):
            if isinstance(item, dict):

                if "text" in item:
                    text_content = item["text"]

                    # Check if this text is actually a JSON-stringified response
                    if text_content.strip().startswith('{'):
                        try:
                            parsed_json = json.loads(text_content)

                            if isinstance(parsed_json, dict):
                                # Handle Google search results with images (URL-based)
                                if "images" in parsed_json and isinstance(parsed_json["images"], list):
                                    for img in parsed_json["images"]:
                                        if isinstance(img, dict) and "link" in img:
                                            result_images.append({
                                                "type": "url",
                                                "url": img.get("link"),
                                                "thumbnail": img.get("thumbnail"),
                                                "title": img.get("title", ""),
                                                "width": img.get("width", 0),
                                                "height": img.get("height", 0)
                                            })

                                # Handle structured tool response: {"text": "...", "metadata": {...}}
                                if "text" in parsed_json:
                                    result_text += parsed_json["text"]
                                    if "metadata" in parsed_json and isinstance(parsed_json["metadata"], dict):
                                        if "metadata" not in tool_result:
                                            tool_result["metadata"] = {}
                                        tool_result["metadata"].update(parsed_json["metadata"])
                                    continue

                                # Handle MCP response format: {"status": "...", "content": [...]}
                                if "content" in parsed_json and isinstance(parsed_json["content"], list):
                                    for unwrapped_item in parsed_json["content"]:
                                        if isinstance(unwrapped_item, dict):
                                            if "text" in unwrapped_item:
                                                result_text += unwrapped_item["text"]
                                            elif "image" in unwrapped_item and "source" in unwrapped_item["image"]:
                                                image_source = unwrapped_item["image"]["source"]
                                                image_data = ""

                                                if "data" in image_source:
                                                    image_data = image_source["data"]
                                                elif "bytes" in image_source:
                                                    if isinstance(image_source["bytes"], bytes):
                                                        image_data = base64.b64encode(image_source["bytes"]).decode('utf-8')
                                                    else:
                                                        image_data = str(image_source["bytes"])

                                                if image_data:
                                                    result_images.append({
                                                        "format": unwrapped_item["image"].get("format", "png"),
                                                        "data": image_data
                                                    })
                                            elif "document" in unwrapped_item:
                                                pass
                                    continue

                        except json.JSONDecodeError:
                            pass

                    # Normal text processing (if not unwrapped)
                    result_text += text_content

                elif "image" in item:
                    if "source" in item["image"]:
                        image_source = item["image"]["source"]
                        image_data = ""

                        if "data" in image_source:
                            image_data = image_source["data"]
                        elif "bytes" in image_source:
                            if isinstance(image_source["bytes"], bytes):
                                image_data = base64.b64encode(image_source["bytes"]).decode('utf-8')
                            else:
                                image_data = str(image_source["bytes"])

                        if image_data:
                            result_images.append({
                                "format": item["image"].get("format", "png"),
                                "data": image_data
                            })

                elif "document" in item:
                    doc_info = item["document"]
                    doc_name = doc_info.get("name", "unknown")
                    doc_format = doc_info.get("format", "unknown")
                    logger.info(f"[Document] Skipping document bytes from frontend display: {doc_name}.{doc_format}")

    return result_text, result_images


def _extract_images_from_json_response(response_data):
    """Extract images from any JSON tool response automatically."""
    images = []

    if isinstance(response_data, dict):
        image_fields = ['screenshot', 'image', 'diagram', 'chart', 'visualization', 'figure']

        for field in image_fields:
            if field in response_data and isinstance(response_data[field], dict):
                img_data = response_data[field]

                # Handle lightweight screenshot format (Nova Act optimized) — no actual image data
                if img_data.get("available") and "description" in img_data:
                    logger.debug(f"Found optimized screenshot reference: {img_data.get('description')}")
                    continue

                # Handle legacy format with actual base64 data
                elif "data" in img_data and "format" in img_data:
                    images.append({
                        "format": img_data["format"],
                        "data": img_data["data"]
                    })

        if "images" in response_data and isinstance(response_data["images"], list):
            images.extend(response_data["images"])

    return images


def _clean_result_text_for_display(original_text: str, parsed_result: dict) -> str:
    """Clean result text by removing large image data but keeping other information."""
    try:
        cleaned_result = copy.deepcopy(parsed_result)

        image_fields = ['screenshot', 'image', 'diagram', 'chart', 'visualization', 'figure']

        for field in image_fields:
            if field in cleaned_result and isinstance(cleaned_result[field], dict):
                if "data" in cleaned_result[field]:
                    data_size = len(cleaned_result[field]["data"])
                    cleaned_result[field] = {
                        "format": cleaned_result[field].get("format", "unknown"),
                        "size": f"{data_size} characters",
                        "note": "Image data extracted and displayed separately"
                    }

        return json.dumps(cleaned_result, indent=2)

    except Exception:
        return original_text


def _process_json_content(result_text: str) -> Tuple[List[Dict[str, str]], str]:
    """Process JSON content to extract screenshots and clean text."""
    try:
        parsed_result = json.loads(result_text)
        extracted_images = _extract_images_from_json_response(parsed_result)

        if extracted_images:
            cleaned_text = _clean_result_text_for_display(result_text, parsed_result)
            return extracted_images, cleaned_text
        else:
            return [], result_text

    except (json.JSONDecodeError, TypeError):
        return [], result_text


def extract_all_content(tool_result: Dict[str, Any]) -> Tuple[str, List[Dict[str, str]]]:
    """Extract text content and images from tool result and process Base64."""
    result_text, result_images = extract_basic_content(tool_result)

    json_images, cleaned_text = _process_json_content(result_text)
    result_images.extend(json_images)

    return cleaned_text, result_images


def extract_metadata_from_json_result(tool_result: Dict[str, Any], result_text: str) -> str:
    """
    Extract metadata from JSON-wrapped result text produced by build_success_response
    or build_image_response.  These helpers embed metadata inside the content text as
    {"text": "...", "metadata": {...}} because the Strands SDK -> Bedrock toolResult
    pipeline drops top-level metadata fields.

    This method:
    1. Merges the embedded metadata dict into tool_result["metadata"]
    2. Returns the unwrapped "text" value so downstream consumers see clean text
    """
    try:
        parsed = json.loads(result_text)

        if isinstance(parsed, dict):
            if "metadata" in parsed and isinstance(parsed["metadata"], dict):
                if "metadata" not in tool_result:
                    tool_result["metadata"] = {}
                tool_result["metadata"].update(parsed["metadata"])

                if "text" in parsed:
                    return parsed["text"]

            # Legacy: browser_session_arn at top level (older A2A format)
            browser_session_arn = parsed.get("browser_session_arn")
            if browser_session_arn:
                if "metadata" not in tool_result:
                    tool_result["metadata"] = {}
                tool_result["metadata"]["browserSessionId"] = browser_session_arn

                if "text" in parsed:
                    return parsed["text"]

    except (json.JSONDecodeError, TypeError):
        pass

    return result_text


def create_tool_result_event(tool_result: Dict[str, Any]) -> str:
    """Create legacy SSE tool_result event. Used by tests for full-pipeline validation."""
    if isinstance(tool_result, str):
        try:
            tool_result = json.loads(tool_result)
        except json.JSONDecodeError:
            tool_result = {"toolUseId": "unknown", "content": [{"text": str(tool_result)}]}

    # Unwrap Lambda response if present
    if "content" in tool_result and isinstance(tool_result["content"], list):
        if tool_result["content"]:
            first_item = tool_result["content"][0]
            if isinstance(first_item, dict) and "text" in first_item:
                text_content = first_item["text"]
                if text_content and text_content.strip():
                    try:
                        parsed = json.loads(text_content)
                        if isinstance(parsed, dict) and "statusCode" in parsed and "body" in parsed:
                            body = json.loads(parsed["body"]) if isinstance(parsed["body"], str) else parsed["body"]
                            if "content" in body:
                                tool_result["content"] = body["content"]
                    except (json.JSONDecodeError, KeyError):
                        pass

    result_text, result_images = extract_all_content(tool_result)
    result_text = extract_metadata_from_json_result(tool_result, result_text)

    tool_result_data = {
        "type": "tool_result",
        "toolUseId": tool_result.get("toolUseId"),
        "result": result_text,
    }
    if result_images:
        tool_result_data["images"] = result_images
    if "status" in tool_result:
        tool_result_data["status"] = tool_result["status"]
    if "metadata" in tool_result:
        tool_result_data["metadata"] = tool_result["metadata"]

    return f"data: {json.dumps(tool_result_data)}\n\n"


# ================================================================== #
# AG-UI Formatter Class                                                #
# ================================================================== #

class AGUIStreamEventFormatter:
    """
    Formats streaming events as AG-UI protocol SSE blobs.

    Stateful: tracks the current run_id, thread_id, and any open text message
    so that AG-UI start/end pairs are always properly matched.

    Usage::

        encoder = EventEncoder()
        formatter = AGUIStreamEventFormatter(encoder)
        sse = formatter.format_event("init")
        sse += formatter.format_event("response", text="Hello")
        sse += formatter.format_event("complete", message="Hello", images=None, usage=None)
    """

    def __init__(self, encoder: EventEncoder, thread_id: Optional[str] = None, run_id: Optional[str] = None) -> None:
        self.encoder = encoder
        self._thread_id: str = thread_id or str(uuid.uuid4())
        self._initial_run_id: Optional[str] = run_id
        self._run_id: Optional[str] = None
        self._current_message_id: Optional[str] = None
        self._message_open: bool = False
        self._started_tool_calls: set[str] = set()
        self._ended_tool_calls: set[str] = set()
        self._tool_call_names: Dict[str, str] = {}
        self._terminal_emitted: bool = False
        self._reasoning_open: bool = False
        self._reasoning_message_id: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _encode(self, event) -> str:
        return self.encoder.encode(event)

    def _close_open_message(self) -> str:
        """Emit TextMessageEndEvent if a text message is currently open."""
        if self._message_open and self._current_message_id:
            encoded = self._encode(TextMessageEndEvent(
                type=EventType.TEXT_MESSAGE_END,
                message_id=self._current_message_id,
            ))
            self._message_open = False
            self._current_message_id = None
            return encoded
        return ""

    def _close_open_reasoning(self) -> str:
        if not self._reasoning_open or not self._reasoning_message_id:
            return ""
        message_id = self._reasoning_message_id
        self._reasoning_open = False
        self._reasoning_message_id = None
        return (
            self._encode(ReasoningMessageEndEvent(
                type=EventType.REASONING_MESSAGE_END,
                message_id=message_id,
            ))
            + self._encode(ReasoningEndEvent(
                type=EventType.REASONING_END,
                message_id=message_id,
            ))
        )

    # ------------------------------------------------------------------ #
    # Public dispatch                                                      #
    # ------------------------------------------------------------------ #

    def format_event(self, event_type: str, **kwargs) -> str:
        """Return an AG-UI-encoded SSE string for the given event type.

        Maps custom event types to AG-UI equivalents:
          init                -> RunStartedEvent
          thinking            -> CustomEvent(name='thinking')
          reasoning           -> CustomEvent(name='reasoning')
          response            -> TextMessageStartEvent + TextMessageContentEvent
          complete            -> (TextMessageEndEvent if message open, else TextMessage if message kwarg provided) + RunFinishedEvent
          tool_use            -> ToolCallStartEvent + ToolCallArgsEvent + ToolCallEndEvent
          tool_call_start     -> ToolCallStartEvent
          tool_call_args      -> ToolCallArgsEvent
          tool_call_end       -> ToolCallEndEvent
          tool_call_name_update -> CustomEvent(name='tool_call_name_update')
          tool_result         -> ToolCallResultEvent
          error               -> RunErrorEvent
          <all others>        -> CustomEvent(name=<original_type>, value=<payload>)
        """
        if self._terminal_emitted and event_type != "init":
            return ""

        _dispatch: Dict[str, Any] = {
            "init":        self._format_init,
            "thinking":    self._format_thinking,
            "reasoning":   self._format_reasoning,
            "interrupt":   self._format_interrupt,
            "response":    self._format_response,
            "complete":    self._format_complete,
            "stop":        self._format_stop,
            "tool_use":    self._format_tool_use,
            "tool_call_start": self._format_tool_call_start,
            "tool_call_args": self._format_tool_call_args,
            "tool_call_end": self._format_tool_call_end,
            "tool_call_name_update": self._format_tool_call_name_update,
            "tool_result": self._format_tool_result,
            "error":       self._format_error,
        }
        handler = _dispatch.get(event_type, self._format_custom)
        prefix = ""
        if event_type != "reasoning":
            prefix = self._close_open_reasoning()
        return prefix + handler(event_type=event_type, **kwargs)

    # ------------------------------------------------------------------ #
    # Core AG-UI event formatters                                          #
    # ------------------------------------------------------------------ #

    def _format_init(self, event_type: str = "init", **kwargs) -> str:
        self._run_id = self._initial_run_id or str(uuid.uuid4())
        self._initial_run_id = None  # consume once; subsequent calls (shouldn't happen) get a fresh uuid
        self._terminal_emitted = False
        self._reasoning_open = False
        self._reasoning_message_id = None
        self._message_open = False
        self._current_message_id = None
        self._started_tool_calls.clear()
        self._ended_tool_calls.clear()
        self._tool_call_names.clear()
        return self._encode(RunStartedEvent(
            type=EventType.RUN_STARTED,
            thread_id=self._thread_id,
            run_id=self._run_id,
        ))

    def _format_thinking(self, event_type: str = "thinking", **kwargs) -> str:
        return self._encode(CustomEvent(
            type=EventType.CUSTOM,
            name="thinking",
            value={"message": kwargs.get("message", "Processing your request...")},
        ))

    def _format_reasoning(self, event_type: str = "reasoning", **kwargs) -> str:
        if self._terminal_emitted:
            return ""
        text = kwargs.get("reasoning_text", kwargs.get("text", ""))
        result = ""
        if not self._reasoning_open:
            self._reasoning_message_id = str(uuid.uuid4())
            self._reasoning_open = True
            result += self._encode(ReasoningStartEvent(
                type=EventType.REASONING_START,
                message_id=self._reasoning_message_id,
            ))
            result += self._encode(ReasoningMessageStartEvent(
                type=EventType.REASONING_MESSAGE_START,
                message_id=self._reasoning_message_id,
                role="reasoning",
            ))
        if text:
            result += self._encode(ReasoningMessageContentEvent(
                type=EventType.REASONING_MESSAGE_CONTENT,
                message_id=self._reasoning_message_id,
                delta=text,
            ))
        return result

    def _format_interrupt(self, event_type: str = "interrupt", **kwargs) -> str:
        if self._terminal_emitted:
            return ""

        raw_interrupts = kwargs.get("interrupts") or []
        standard_interrupts = []
        for raw in raw_interrupts:
            raw_reason = raw.get("reason")
            reason = (
                raw_reason
                if isinstance(raw_reason, str)
                else json.dumps(raw_reason or {}, ensure_ascii=False)
            )
            standard_interrupts.append(Interrupt(
                id=str(raw.get("id") or uuid.uuid4()),
                reason=reason,
                message=raw.get("name"),
                tool_call_id=raw.get("toolCallId"),
                metadata={
                    "name": raw.get("name"),
                    "reason": raw_reason,
                },
            ))

        result = self._close_open_message()
        result += self._encode(CustomEvent(
            type=EventType.CUSTOM,
            name="interrupt",
            value={"interrupts": raw_interrupts},
        ))
        run_id = self._run_id or str(uuid.uuid4())
        result += self._encode(RunFinishedEvent(
            type=EventType.RUN_FINISHED,
            thread_id=self._thread_id,
            run_id=run_id,
            outcome=RunFinishedInterruptOutcome(
                interrupts=standard_interrupts,
            ),
        ))
        self._terminal_emitted = True
        return result

    def _format_response(self, event_type: str = "response", **kwargs) -> str:
        text = kwargs.get("text", "")
        result = ""
        if not self._message_open:
            self._current_message_id = str(uuid.uuid4())
            self._message_open = True
            result += self._encode(TextMessageStartEvent(
                type=EventType.TEXT_MESSAGE_START,
                message_id=self._current_message_id,
                role="assistant",
            ))
        result += self._encode(TextMessageContentEvent(
            type=EventType.TEXT_MESSAGE_CONTENT,
            message_id=self._current_message_id,
            delta=text,
        ))
        return result

    def _format_complete(self, event_type: str = "complete", **kwargs) -> str:
        if self._terminal_emitted:
            return ""

        message: str = kwargs.get("message", "")
        result = ""

        if message and not self._message_open:
            # No incremental text was streamed — emit the final result text as a
            # complete text message now so it is not silently dropped.
            msg_id = str(uuid.uuid4())
            result += self._encode(TextMessageStartEvent(
                type=EventType.TEXT_MESSAGE_START,
                message_id=msg_id,
                role="assistant",
            ))
            result += self._encode(TextMessageContentEvent(
                type=EventType.TEXT_MESSAGE_CONTENT,
                message_id=msg_id,
                delta=message,
            ))
            result += self._encode(TextMessageEndEvent(
                type=EventType.TEXT_MESSAGE_END,
                message_id=msg_id,
            ))
        else:
            # Close any message that was built up through incremental response events.
            result += self._close_open_message()

        # Images and usage have no standard AG-UI home; relay as a CustomEvent
        # before the terminal event so every consumer can observe them.
        images = kwargs.get("images")
        usage = kwargs.get("usage")
        if images or usage:
            extra: Dict[str, Any] = {}
            if images:
                extra["images"] = images
            if usage:
                extra["usage"] = usage
            result += self._encode(CustomEvent(
                type=EventType.CUSTOM,
                name="complete_metadata",
                value=extra,
            ))
        run_id = self._run_id or str(uuid.uuid4())
        result += self._encode(RunFinishedEvent(
            type=EventType.RUN_FINISHED,
            thread_id=self._thread_id,
            run_id=run_id,
        ))
        self._terminal_emitted = True
        return result

    def _format_tool_use(self, event_type: str = "tool_use", **kwargs) -> str:
        """Format a complete tool call for non-streaming compatibility paths."""
        tool_use = kwargs.get("tool_use")
        if not isinstance(tool_use, dict):
            tool_use = kwargs
        tool_use_id: str = tool_use.get("toolUseId") or str(uuid.uuid4())
        tool_name: str = tool_use.get("name", "unknown")
        tool_input = tool_use.get("input", {})

        wire_tool_name = tool_name
        if tool_name == "skill_executor" and isinstance(tool_input, dict):
            inner = (
                tool_input.get("tool_name")
                or tool_input.get("script_name")
                or tool_input.get("skill_name")
                or "skill"
            )
            if isinstance(inner, str) and inner:
                wire_tool_name = inner

        result = ""
        result += self._format_tool_call_start(
            tool_call_id=tool_use_id,
            tool_call_name=wire_tool_name,
        )
        result += self._format_tool_call_name_update(
            tool_call_id=tool_use_id,
            tool_call_name=wire_tool_name,
        )
        result += self._format_tool_call_args(
            tool_call_id=tool_use_id,
            delta=json.dumps(tool_input),
        )
        result += self._format_tool_call_end(tool_call_id=tool_use_id)
        return result

    def _format_tool_call_start(
        self,
        event_type: str = "tool_call_start",
        **kwargs,
    ) -> str:
        tool_call_id = kwargs.get("tool_call_id") or kwargs.get("toolCallId")
        tool_call_name = (
            kwargs.get("tool_call_name")
            or kwargs.get("toolCallName")
            or "unknown"
        )
        if not tool_call_id or tool_call_id in self._started_tool_calls:
            return ""

        self._started_tool_calls.add(tool_call_id)
        self._tool_call_names[tool_call_id] = tool_call_name
        self._ended_tool_calls.discard(tool_call_id)
        return self._close_open_message() + self._encode(ToolCallStartEvent(
            type=EventType.TOOL_CALL_START,
            tool_call_id=tool_call_id,
            tool_call_name=tool_call_name,
            parent_message_id=None,
        ))

    def _format_tool_call_args(
        self,
        event_type: str = "tool_call_args",
        **kwargs,
    ) -> str:
        tool_call_id = kwargs.get("tool_call_id") or kwargs.get("toolCallId")
        delta = kwargs.get("delta", "")
        if (
            not tool_call_id
            or tool_call_id not in self._started_tool_calls
            or tool_call_id in self._ended_tool_calls
            or not delta
        ):
            return ""
        return self._encode(ToolCallArgsEvent(
            type=EventType.TOOL_CALL_ARGS,
            tool_call_id=tool_call_id,
            delta=delta,
        ))

    def _format_tool_call_end(
        self,
        event_type: str = "tool_call_end",
        **kwargs,
    ) -> str:
        tool_call_id = kwargs.get("tool_call_id") or kwargs.get("toolCallId")
        if (
            not tool_call_id
            or tool_call_id not in self._started_tool_calls
            or tool_call_id in self._ended_tool_calls
        ):
            return ""
        self._ended_tool_calls.add(tool_call_id)
        return self._encode(ToolCallEndEvent(
            type=EventType.TOOL_CALL_END,
            tool_call_id=tool_call_id,
        ))

    def _format_tool_call_name_update(
        self,
        event_type: str = "tool_call_name_update",
        **kwargs,
    ) -> str:
        tool_call_id = kwargs.get("tool_call_id") or kwargs.get("toolCallId")
        tool_call_name = kwargs.get("tool_call_name") or kwargs.get("toolCallName")
        if (
            not tool_call_id
            or not tool_call_name
            or tool_call_id not in self._started_tool_calls
            or self._tool_call_names.get(tool_call_id) == tool_call_name
        ):
            return ""
        self._tool_call_names[tool_call_id] = tool_call_name
        return self._encode(CustomEvent(
            type=EventType.CUSTOM,
            name="tool_call_name_update",
            value={
                "toolCallId": tool_call_id,
                "toolCallName": tool_call_name,
            },
        ))

    def _format_tool_result(self, event_type: str = "tool_result", **kwargs) -> str:
        # Accept either format_event("tool_result", tool_result={...})
        # or     format_event("tool_result", toolUseId=..., content=[...], ...)
        tool_result = kwargs.get("tool_result")
        if tool_result is None:
            tool_result = kwargs

        # Handle the rare case where tool_result arrives as a JSON string
        if isinstance(tool_result, str):
            try:
                tool_result = json.loads(tool_result)
            except json.JSONDecodeError:
                tool_result = {"toolUseId": "unknown", "content": [{"text": tool_result}]}

        # Shallow copy so we don't mutate the caller's dict
        tool_result = dict(tool_result)

        # Unwrap Lambda response envelope (Gateway tools)
        # Lambda format: content[0].text = '{"statusCode":200,"body":"..."}'
        if "content" in tool_result and isinstance(tool_result["content"], list):
            if tool_result["content"]:
                first = tool_result["content"][0]
                if isinstance(first, dict) and "text" in first:
                    text_content = first["text"]
                    if text_content and text_content.strip():
                        try:
                            parsed = json.loads(text_content)
                            if isinstance(parsed, dict) and "statusCode" in parsed and "body" in parsed:
                                body = (
                                    json.loads(parsed["body"])
                                    if isinstance(parsed["body"], str)
                                    else parsed["body"]
                                )
                                if "content" in body:
                                    tool_result["content"] = body["content"]
                        except (json.JSONDecodeError, KeyError):
                            pass

        # Parse tool result content
        result_text, result_images = extract_all_content(tool_result)
        result_text = extract_metadata_from_json_result(tool_result, result_text)

        # Build a structured payload that carries all the same fields the original
        # tool_result SSE blob would have contained.
        payload: Dict[str, Any] = {"result": result_text}
        if result_images:
            payload["images"] = result_images
        if "status" in tool_result:
            payload["status"] = tool_result["status"]
        if "metadata" in tool_result:
            payload["metadata"] = tool_result["metadata"]

        return self._encode(ToolCallResultEvent(
            type=EventType.TOOL_CALL_RESULT,
            message_id=str(uuid.uuid4()),
            tool_call_id=tool_result.get("toolUseId", "unknown"),
            content=json.dumps(payload),
        ))

    def _format_stop(self, event_type: str = "stop", **kwargs) -> str:
        """Emitted when the stream is gracefully stopped by the user."""
        if self._terminal_emitted:
            return ""

        result = self._close_open_message()
        result += self._encode(CustomEvent(
            type=EventType.CUSTOM,
            name="stream_stopped",
            value={"message": "Stream stopped by user"},
        ))
        run_id = self._run_id or str(uuid.uuid4())
        result += self._encode(RunFinishedEvent(
            type=EventType.RUN_FINISHED,
            thread_id=self._thread_id,
            run_id=run_id,
        ))
        self._terminal_emitted = True
        return result

    def _format_error(self, event_type: str = "error", **kwargs) -> str:
        if self._terminal_emitted:
            return ""

        result = self._close_open_message()
        result += self._encode(RunErrorEvent(
            type=EventType.RUN_ERROR,
            message=kwargs.get("error_message", kwargs.get("message", "Unknown error")),
        ))
        self._terminal_emitted = True
        return result

    # ------------------------------------------------------------------ #
    # App-specific events -> CustomEvent passthrough                        #
    # ------------------------------------------------------------------ #

    def _format_custom(self, event_type: str = "custom", **kwargs) -> str:
        """Handles all app-specific events without a standard AG-UI equivalent.

        Covers: interrupt, warning, metadata, oauth_elicitation,
        browser_progress, research_progress, code_step, code_todo_update,
        code_result_meta, artifact_created, start, end.
        """
        return self._encode(CustomEvent(
            type=EventType.CUSTOM,
            name=event_type,
            value=kwargs,
        ))
