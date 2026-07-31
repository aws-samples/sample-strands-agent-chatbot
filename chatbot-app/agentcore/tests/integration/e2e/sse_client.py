"""SSE client that POSTs to the BFF /api/stream/chat endpoint and collects events.

The BFF accepts AG-UI RunAgentInput (camelCase `threadId`/`runId`) and forwards
to the AgentCore runtime as snake_case. See
`chatbot-app/frontend/src/app/api/stream/chat/route.ts`.

Event shape (subset used here): each `data: {...}` frame is a JSON object with a
`type` field — notably `TOOL_CALL_START` (carries `tool_call_name`),
`TOOL_CALL_RESULT` (carries `content`), `TEXT_MESSAGE_CONTENT` (carries `delta`),
and `RUN_FINISHED`. See `src/streaming/agui_event_formatter.py`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx


@dataclass
class ToolInvocation:
    """A single observed tool invocation, flattened for easy assertion.

    `tool_call_name` is the effective tool name from TOOL_CALL_START — e.g.
    `arxiv_search`, `create_visualization`, or `skill_dispatcher` for the
    L2 meta-tool. `skill_name` / `inner_tool_name` are parsed from the args
    delta for dispatcher calls (and for skill_executor calls from older
    backend builds, kept as a no-op safety net).
    """
    tool_call_id: str
    tool_call_name: str
    args_raw: str = ""
    skill_name: str | None = None
    inner_tool_name: str | None = None
    result_preview: str | None = None
    is_error: bool = False

    @property
    def effective_name(self) -> str:
        """The name a prompt would actually be trying to exercise."""
        return self.inner_tool_name or self.skill_name or self.tool_call_name


@dataclass
class StreamResult:
    events: list[dict[str, Any]]
    thread_id: str
    run_id: str
    session_id_header: str | None = None
    raw_error_events: list[dict[str, Any]] = field(default_factory=list)

    def invocations(self) -> list[ToolInvocation]:
        """Flatten SSE events into a per-tool-call summary.

        AG-UI emits camelCase fields on the wire (`toolCallName`,
        `toolCallId`) because ag_ui.core uses `alias_generator=to_camel`.

        For each tool call the backend emits a START→ARGS→END triple twice:
        first with an empty `{}` args delta (right after the LLM produces the
        tool_use block header), then again with the full argument payload.
        We don't try to reconstruct a streaming JSON — we just collect every
        delta per toolCallId and pick the last one that parses.
        """
        by_id: dict[str, ToolInvocation] = {}
        deltas: dict[str, list[str]] = {}
        order: list[str] = []
        for e in self.events:
            t = e.get("type")
            tid = e.get("toolCallId")
            if t == "TOOL_CALL_START" and tid:
                name = e.get("toolCallName", "") or ""
                if tid not in by_id:
                    by_id[tid] = ToolInvocation(
                        tool_call_id=tid,
                        tool_call_name=name,
                    )
                    deltas[tid] = []
                    order.append(tid)
                elif name:
                    # Strands emits START twice per tool call: the first time
                    # before the model has filled in arguments (skill_executor
                    # with empty input), the second after (the unwrapped
                    # effective name). The last non-empty name wins.
                    by_id[tid].tool_call_name = name
            elif t == "TOOL_CALL_ARGS" and tid and tid in by_id:
                delta = e.get("delta", "")
                if delta:
                    deltas[tid].append(delta)
            elif t == "TOOL_CALL_RESULT" and tid and tid in by_id:
                inv = by_id[tid]
                content = e.get("content")
                preview = str(content)[:400] if content is not None else ""
                inv.result_preview = preview
                lowered = preview.lower()
                inv.is_error = (
                    '"error"' in lowered
                    or '"status": "error"' in lowered
                    or '"statuscode": 5' in lowered
                )

        for tid, inv in by_id.items():
            parts = deltas.get(tid, [])
            inv.args_raw = parts[-1] if parts else ""
            # Prefer the last delta that parses as JSON; fall back to the raw last delta.
            parsed: Any = None
            for part in reversed(parts):
                try:
                    candidate = json.loads(part)
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict):
                    parsed = candidate
                    break
            if isinstance(parsed, dict) and inv.tool_call_name in (
                "skill_dispatcher", "skill_executor",
            ):
                inv.skill_name = parsed.get("skill_name")
                inv.inner_tool_name = parsed.get("tool_name")

        return [by_id[tid] for tid in order]

    def tool_call_names(self) -> list[str]:
        return [inv.tool_call_name for inv in self.invocations()]

    def effective_tool_names(self) -> list[str]:
        """Flattened names including skill/inner-tool for skill_* calls."""
        out: list[str] = []
        for inv in self.invocations():
            out.append(inv.tool_call_name)
            if inv.skill_name:
                out.append(inv.skill_name)
            if inv.inner_tool_name:
                out.append(inv.inner_tool_name)
        return out

    def assistant_text(self) -> str:
        return "".join(
            e.get("delta", "")
            for e in self.events
            if e.get("type") == "TEXT_MESSAGE_CONTENT"
        )

    def tool_result_contents(self) -> list[str]:
        """Return serialized TOOL_CALL_RESULT content payloads."""
        return [
            str(event.get("content", ""))
            for event in self.events
            if event.get("type") == "TOOL_CALL_RESULT"
        ]

    def run_finished(self) -> bool:
        return any(e.get("type") == "RUN_FINISHED" for e in self.events)

    def interrupted_for_approval(self) -> bool:
        """True if the run paused on a user-approval interrupt (e.g.
        `chatbot-research-approval` for the research agent).

        These runs never emit RUN_FINISHED by design — the backend waits for
        the frontend to approve/reject the proposed action. For e2e path
        coverage, reaching the interrupt is success: the tool routing worked,
        the skill was dispatched, and the before-tool-call hook fired.
        """
        for e in self.events:
            if e.get("type") != "CUSTOM":
                continue
            if e.get("name") == "interrupt":
                val = e.get("value") or {}
                interrupts = val.get("interrupts") or []
                if any(isinstance(i, dict) for i in interrupts):
                    return True
        return False

    def interrupts(self) -> list[dict[str, Any]]:
        """Return all interrupt payloads emitted during this run."""
        interrupts: list[dict[str, Any]] = []
        for event in self.events:
            if event.get("type") != "CUSTOM" or event.get("name") != "interrupt":
                continue
            value = event.get("value") or {}
            interrupts.extend(
                item for item in (value.get("interrupts") or [])
                if isinstance(item, dict)
            )
        return interrupts

    def custom_events(self, name: str) -> list[dict[str, Any]]:
        """Return CUSTOM events with the requested name."""
        return [
            event for event in self.events
            if event.get("type") == "CUSTOM" and event.get("name") == name
        ]

    def terminated_cleanly(self) -> bool:
        return self.run_finished() or self.interrupted_for_approval()


def _make_thread_id() -> str:
    # >=33 chars required by the BFF / AgentCore session validation.
    return f"e2e_{uuid4().hex}_{uuid4().hex}"[:64]


def stream_chat(
    bff_url: str,
    token: str,
    prompt: str,
    *,
    thread_id: str | None = None,
    model_id: str = "openai.gpt-5.6-terra",
    state_overrides: dict[str, Any] | None = None,
    timeout: float = 180.0,
) -> StreamResult:
    """POST to {bff_url}/api/stream/chat and collect AG-UI SSE events."""
    url = bff_url.rstrip("/") + "/api/stream/chat"
    tid = thread_id or _make_thread_id()
    rid = f"run_{uuid4().hex}"

    state: dict[str, Any] = {"model_id": model_id}
    if state_overrides:
        state.update(state_overrides)

    body = {
        "threadId": tid,
        "runId": rid,
        "messages": [
            {
                "id": f"msg_{uuid4().hex}",
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            }
        ],
        "state": state,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    session_header: str | None = None

    with httpx.Client(timeout=timeout) as client:
        with client.stream("POST", url, json=body, headers=headers) as resp:
            resp.raise_for_status()
            session_header = resp.headers.get("X-Session-ID")
            buf = ""
            for chunk in resp.iter_text():
                if not chunk:
                    continue
                buf += chunk
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    for line in frame.splitlines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[len("data:"):].strip()
                        if not payload:
                            continue
                        try:
                            evt = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        events.append(evt)
                        t = evt.get("type")
                        if t == "error":
                            errors.append(evt)
                        if t in ("RUN_FINISHED", "RUN_ERROR"):
                            # keep reading to drain — but we can also return here
                            pass

    return StreamResult(
        events=events,
        thread_id=tid,
        run_id=rid,
        session_id_header=session_header,
        raw_error_events=errors,
    )
