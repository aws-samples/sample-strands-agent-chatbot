#!/usr/bin/env python3
"""Exercise deployed Code and Research Agent model-routing paths."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid


ORCHESTRATOR_URL = os.environ["ORCH_URL"]
ACCESS_TOKEN = os.environ["ACCESS_TOKEN"]
MODEL_ID = os.environ.get("MODEL_SMOKE_MODEL_ID", "openai.gpt-5.6-terra")


def _thread_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}_{uuid.uuid4().hex}"[:64]


def _invoke(prompt: str, thread_id: str) -> list[dict]:
    payload = {
        "thread_id": thread_id,
        "run_id": f"run_{uuid.uuid4().hex}",
        "messages": [{
            "id": f"msg_{uuid.uuid4().hex}",
            "role": "user",
            "content": [{"type": "text", "text": prompt}],
        }],
        "tools": [],
        "context": [],
        "state": {
            "model_id": MODEL_ID,
            "user_id": "model-smoke-test",
        },
    }
    request = urllib.request.Request(
        ORCHESTRATOR_URL,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": thread_id,
        },
    )

    events = []
    try:
        with urllib.request.urlopen(request, timeout=700) as response:
            for raw_line in response:
                line = raw_line.decode().strip()
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if not data:
                    continue
                try:
                    events.append(json.loads(data))
                except json.JSONDecodeError:
                    continue
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {body[:500]}") from error
    return events


def _assert_finished(events: list[dict], label: str) -> None:
    errors = [
        event for event in events
        if event.get("type") in {"RUN_ERROR", "error"}
    ]
    if errors:
        raise RuntimeError(f"{label} emitted errors: {errors[-3:]}")
    if not any(event.get("type") == "RUN_FINISHED" for event in events):
        raise RuntimeError(f"{label} did not emit RUN_FINISHED")


def _tool_names(events: list[dict]) -> set[str]:
    return {
        event.get("toolCallName", "")
        for event in events
        if event.get("type") == "TOOL_CALL_START"
    }


def _interrupts(events: list[dict]) -> list[dict]:
    result = []
    for event in events:
        if event.get("type") != "CUSTOM" or event.get("name") != "interrupt":
            continue
        result.extend((event.get("value") or {}).get("interrupts") or [])
    return result


def main() -> int:
    code_events = _invoke(
        "Delegate to the coding agent: create model-smoke.txt containing exactly "
        "MODEL_SMOKE_OK, verify it, and report completion.",
        _thread_id("code_smoke"),
    )
    _assert_finished(code_events, "Code Agent")
    if "code_agent" not in _tool_names(code_events):
        raise RuntimeError(f"Code Agent tool was not called: {_tool_names(code_events)}")
    print(f"  Code Agent -> completed with {MODEL_ID} as orchestrator model")

    research_thread = _thread_id("research_smoke")
    approval_events = _invoke(
        "Use the research agent for a concise multi-source comparison of HTTP/2 "
        "and HTTP/3 with three technical differences and sources.",
        research_thread,
    )
    interrupts = _interrupts(approval_events)
    if not interrupts:
        raise RuntimeError("Research Agent approval interrupt was not emitted")
    interrupt_id = interrupts[0].get("id") or interrupts[0].get("interruptId")
    if not interrupt_id:
        raise RuntimeError(f"Research interrupt has no ID: {interrupts[0]}")

    approval = json.dumps([{
        "interruptResponse": {
            "interruptId": interrupt_id,
            "response": "approved",
        }
    }])
    research_events = _invoke(approval, research_thread)
    _assert_finished(research_events, "Research Agent")
    progress = [
        event for event in research_events
        if event.get("type") == "CUSTOM"
        and event.get("name") == "research_progress"
    ]
    research_results = [
        str(event.get("content", ""))
        for event in research_events
        if event.get("type") == "TOOL_CALL_RESULT"
        and "<research>" in str(event.get("content", ""))
    ]
    if not progress:
        raise RuntimeError("Research Agent emitted no research_progress events")
    if not research_results:
        raise RuntimeError("Research Agent returned no research artifact")
    if (
        research_results[0].count("<research>") != 1
        or research_results[0].count("</research>") != 1
    ):
        raise RuntimeError("Research Agent returned a duplicated research artifact")
    print(f"  Research Agent -> approved and completed with {MODEL_ID}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"  model smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1)
