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


def test_a2a_research_approval_roundtrip(stream):
    """Approve the HITL interrupt and verify the Research Agent completes."""
    first = stream(
        "Use the research agent for a concise multi-source comparison of HTTP/2 "
        "and HTTP/3 with a summary, three technical differences, and sources.",
        timeout=300.0,
    )
    assert first.interrupted_for_approval(), (
        f"Research approval interrupt not observed. Errors: {first.raw_error_events}"
    )
    _assert_skill_or_tool_invoked(first, ("research-agent", "research_agent"))

    interrupt = first.interrupts()[0]
    interrupt_id = interrupt.get("id") or interrupt.get("interruptId")
    assert interrupt_id, f"Interrupt has no ID: {interrupt}"

    approval = json.dumps([{
        "interruptResponse": {
            "interruptId": interrupt_id,
            "response": "approved",
        }
    }])
    completed = stream(
        approval,
        thread_id=first.thread_id,
        timeout=600.0,
    )

    assert completed.run_finished(), (
        f"Research approval continuation did not finish. "
        f"Errors: {completed.raw_error_events}"
    )
    assert not completed.raw_error_events
    assert completed.custom_events("research_progress"), (
        "Research Agent completed without emitting research_progress events"
    )
    research_results = [
        content for content in completed.tool_result_contents()
        if "<research>" in content
    ]
    assert research_results, "Research Agent returned no research artifact"
    assert research_results[0].count("<research>") == 1
    assert research_results[0].count("</research>") == 1
    assert completed.assistant_text().strip(), (
        "Orchestrator returned no completion summary"
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
