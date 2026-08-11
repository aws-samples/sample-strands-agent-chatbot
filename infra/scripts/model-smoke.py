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


def _tool_receipt(events: list[dict], tool_name: str) -> dict:
    tool_call_ids = {
        event.get("toolCallId")
        for event in events
        if event.get("type") == "TOOL_CALL_START"
        and event.get("toolCallName") == tool_name
    }
    for event in events:
        if (
            event.get("type") != "TOOL_CALL_RESULT"
            or event.get("toolCallId") not in tool_call_ids
        ):
            continue
        content = event.get("content")
        if not isinstance(content, str):
            continue
        try:
            wrapper = json.loads(content)
            receipt = wrapper.get("result", wrapper)
            if isinstance(receipt, str):
                receipt = json.loads(receipt)
        except (json.JSONDecodeError, AttributeError):
            continue
        if isinstance(receipt, dict):
            return receipt
    return {}


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
    research_events = _invoke(
        "Use the research agent for a concise multi-source comparison of HTTP/2 "
        "and HTTP/3 with three technical differences and sources.",
        research_thread,
    )
    _assert_finished(research_events, "Research Agent")
    if "research_agent" not in _tool_names(research_events):
        raise RuntimeError(
            f"Research Agent tool was not called: {_tool_names(research_events)}"
        )
    receipt = _tool_receipt(research_events, "research_agent")
    if receipt.get("status") != "started":
        raise RuntimeError(
            f"Research Agent returned no durable start receipt: {receipt}"
        )
    if not receipt.get("job_id") or not receipt.get("artifact_id"):
        raise RuntimeError(f"Research Agent receipt is incomplete: {receipt}")
    print(f"  Research Agent -> durable background job started with {MODEL_ID}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"  model smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1)
