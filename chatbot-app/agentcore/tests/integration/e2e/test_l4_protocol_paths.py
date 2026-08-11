"""L4 agent-turn integration tests, one case per protocol path.

Each case sends a prompt through the deployed BFF and asserts — from the SSE
event stream — that the correct tool was invoked for its protocol. Text
matching is avoided except where a tool choice cannot be observed directly
(memory roundtrip).

Run: `pytest -m e2e tests/integration/e2e -v`
"""

from __future__ import annotations

import json
import os
import time

import httpx
import pytest

pytestmark = pytest.mark.e2e


def _assert_skill_or_tool_invoked(result, substrings: tuple[str, ...]) -> None:
    """Assert the agent exercised the expected protocol path.

    The backend formatter unwraps skill_executor so TOOL_CALL_START carries
    the effective tool name directly. Matching is flattened across the
    top-level name, skill_dispatcher's inner skill_name, and any legacy
    inner_tool_name — so the same assertion works whether the backend is
    on a new or old build.

    For each matched substring we also require at least one invocation that
    did NOT error out — so a broken path fails the test instead of silently
    passing on "the agent at least tried".
    """
    invocations = result.invocations()
    subs = tuple(s.lower() for s in substrings)
    haystacks = [
        (inv, " ".join(filter(None, (
            inv.tool_call_name, inv.skill_name, inv.inner_tool_name
        ))).lower())
        for inv in invocations
    ]
    matching = [inv for inv, hay in haystacks if any(s in hay for s in subs)]
    assert matching, (
        f"Expected a tool/skill matching one of {substrings}. "
        f"Observed invocations: "
        f"{[(inv.tool_call_name, inv.skill_name, inv.inner_tool_name) for inv in invocations]}. "
        f"Errors: {result.raw_error_events}"
    )
    ok = [inv for inv in matching if not inv.is_error and inv.result_preview is not None]
    # Accept approval-interrupted runs: the tool dispatched correctly but was
    # paused before execution. No result_preview will exist.
    if not ok and result.interrupted_for_approval():
        return
    assert ok, (
        f"All matching invocations for {substrings} failed or never returned. "
        f"Previews: {[(inv.effective_name, inv.is_error, (inv.result_preview or '')[:200]) for inv in matching]}"
    )


_RUN_3LO = os.environ.get("RUN_3LO") == "1"


_CASES = [
    pytest.param(
        "Make a simple bar chart of these values: [1, 2, 3, 4, 5] with labels a,b,c,d,e.",
        ("visualization", "create_visualization"),
        {"timeout": 180.0},
        id="local_python_viz",
    ),
    pytest.param(
        "Draw an excalidraw diagram with two boxes labeled A and B connected by an arrow.",
        ("excalidraw", "create_excalidraw_diagram"),
        {"timeout": 180.0},
        id="local_excalidraw",
    ),
    pytest.param(
        "Use the code interpreter to compute 7 * 191 and tell me the result.",
        ("code-interpreter", "execute_code"),
        {"timeout": 240.0},
        id="builtin_code_interp",
    ),
    pytest.param(
        "Open https://example.com in a browser and tell me the page title.",
        ("browser-automation", "browser_act", "browser_get_page_info"),
        {"timeout": 360.0},
        id="builtin_browser",
    ),
    pytest.param(
        "Find recent arxiv papers on Mamba state space models.",
        ("arxiv-search", "arxiv_search"),
        {"timeout": 180.0},
        id="gateway_arxiv",
    ),
    pytest.param(
        "What is the current stock price of AAPL?",
        ("financial-news", "stock_quote", "stock_analysis"),
        {"timeout": 180.0},
        id="gateway_finance",
    ),
    pytest.param(
        "What's the weather in Seoul today?",
        ("weather",),
        {"timeout": 180.0},
        id="gateway_weather",
    ),
    pytest.param(
        "Search the web for the latest news on LLM evaluation benchmarks.",
        ("web-search", "tavily-search", "google-web-search", "web_search", "tavily_search"),
        {"timeout": 180.0},
        id="gateway_web_search",
    ),
    pytest.param(
        "Give me a brief Wikipedia summary of Alan Turing.",
        ("wikipedia",),
        {"timeout": 180.0},
        id="gateway_wikipedia",
    ),
    pytest.param(
        "Delegate to the coding agent: write a FastAPI hello-world endpoint and "
        "save it to the workspace as app.py.",
        ("code-agent", "code_agent"),
        {"timeout": 600.0},
        id="a2a_code",
    ),
    pytest.param(
        "Create a 3-slide PowerPoint presentation titled 'AWS Lambda Basics' "
        "with slides about runtime, pricing, and use cases.",
        ("powerpoint-presentations", "create_presentation"),
        {"timeout": 300.0, "state_overrides": {"request_type": "skill"}},
        id="skill_ppt",
    ),
]

_3LO_CASES = [
    pytest.param(
        "List my 3 most recent Gmail messages.",
        ("gmail_list", "list_messages"),
        {"timeout": 240.0},
        id="gateway_3lo_gmail",
        marks=pytest.mark.skipif(not _RUN_3LO, reason="set RUN_3LO=1 to enable"),
    ),
    pytest.param(
        "List my top 5 GitHub repositories.",
        ("github", "list_repos"),
        {"timeout": 240.0},
        id="gateway_3lo_github",
        marks=pytest.mark.skipif(not _RUN_3LO, reason="set RUN_3LO=1 to enable"),
    ),
]


@pytest.mark.parametrize("prompt,expected_tool_substrings,call_kwargs", _CASES + _3LO_CASES)
def test_protocol_path(stream, prompt, expected_tool_substrings, call_kwargs):
    result = stream(prompt, **call_kwargs)
    assert result.terminated_cleanly(), (
        f"Neither RUN_FINISHED nor an approval interrupt observed. "
        f"Errors: {result.raw_error_events}. "
        f"Last events: {[e.get('type') for e in result.events[-10:]]}"
    )
    _assert_skill_or_tool_invoked(result, expected_tool_substrings)


def _research_start_receipt(result) -> dict:
    for content in result.tool_result_contents():
        try:
            wrapper = json.loads(content)
            receipt = wrapper.get("result", wrapper)
            if isinstance(receipt, str):
                receipt = json.loads(receipt)
        except (json.JSONDecodeError, AttributeError):
            continue
        if isinstance(receipt, dict) and receipt.get("status") == "started":
            return receipt
    return {}


def test_a2a_research_durable_completion(stream, bff_url, cognito_token):
    """Verify research reaches durable artifact and mailbox delivery."""
    started = stream(
        "Use the research agent for a concise multi-source comparison of HTTP/2 "
        "and HTTP/3 with a summary, three technical differences, and sources.",
        timeout=300.0,
    )
    assert started.run_finished(), (
        f"Research start turn did not finish. Errors: {started.raw_error_events}"
    )
    _assert_skill_or_tool_invoked(started, ("research-agent", "research_agent"))
    receipt = _research_start_receipt(started)
    assert receipt.get("job_id"), f"Research returned no durable receipt: {receipt}"
    assert receipt.get("artifact_id"), f"Research receipt is incomplete: {receipt}"

    deadline = time.monotonic() + 600
    job = {}
    with httpx.Client(
        base_url=bff_url,
        headers={"Authorization": f"Bearer {cognito_token}"},
        timeout=30.0,
    ) as client:
        while time.monotonic() < deadline:
            response = client.get(
                "/api/research/jobs",
                params={
                    "session_id": started.thread_id,
                    "job_id": receipt["job_id"],
                    "include_content": "true",
                },
            )
            response.raise_for_status()
            job = response.json().get("job") or {}
            if job.get("status") in {"delivered", "error", "cancelled"}:
                break
            time.sleep(5)

    assert job.get("status") == "delivered", (
        f"Research did not reach mailbox delivery: {job}"
    )
    assert job.get("mailboxEventId") == f"research-result:{receipt['job_id']}"
    artifact = job.get("artifact") or {}
    assert artifact.get("id") == receipt["artifact_id"]
    assert str(artifact.get("content") or "").strip(), (
        "Delivered research job has no hydrated artifact content"
    )


def test_memory_roundtrip(stream):
    """Two turns sharing a thread_id — the second turn must recall the first."""
    from .sse_client import _make_thread_id

    tid = _make_thread_id()
    turn1 = stream(
        "Please remember this fact about me for later: my primary programming "
        "language is Go (Golang). Just acknowledge.",
        thread_id=tid,
        timeout=180.0,
    )
    assert turn1.run_finished(), f"Turn 1 failed: {turn1.raw_error_events}"

    turn2 = stream(
        "Based on what I told you earlier, what is my primary programming language?",
        thread_id=tid,
        timeout=180.0,
    )
    assert turn2.run_finished(), f"Turn 2 failed: {turn2.raw_error_events}"

    text = turn2.assistant_text().lower()
    assert "go" in text or "golang" in text, (
        f"Turn 2 response should mention Go/Golang. Got: {text[:400]!r}"
    )
