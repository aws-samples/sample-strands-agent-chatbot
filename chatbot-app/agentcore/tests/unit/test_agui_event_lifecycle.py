import json

from ag_ui.encoder import EventEncoder

from streaming.agui_event_formatter import AGUIStreamEventFormatter


def _events(stream: str) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in stream.splitlines()
        if line.startswith("data: ")
    ]


def test_complete_metadata_precedes_terminal_event():
    formatter = AGUIStreamEventFormatter(
        EventEncoder(),
        thread_id="thread-1",
        run_id="run-1",
    )

    stream = formatter.format_event("init")
    stream += formatter.format_event(
        "complete",
        message="done",
        images=[{"data": "image"}],
        usage={"totalTokens": 3},
    )
    events = _events(stream)

    metadata_index = next(
        index
        for index, event in enumerate(events)
        if event.get("name") == "complete_metadata"
    )
    finished_index = next(
        index
        for index, event in enumerate(events)
        if event["type"] == "RUN_FINISHED"
    )
    assert metadata_index < finished_index
    assert events[-1]["type"] == "RUN_FINISHED"


def test_terminal_event_is_emitted_only_once():
    formatter = AGUIStreamEventFormatter(
        EventEncoder(),
        thread_id="thread-1",
        run_id="run-1",
    )

    stream = formatter.format_event("init")
    stream += formatter.format_event("complete", message="done")
    stream += formatter.format_event("error", error_message="late failure")
    stream += formatter.format_event("complete", message="duplicate")
    stream += formatter.format_event("browser_progress", content="late event")
    terminal_events = [
        event
        for event in _events(stream)
        if event["type"] in {"RUN_FINISHED", "RUN_ERROR"}
    ]

    assert [event["type"] for event in terminal_events] == ["RUN_FINISHED"]
    assert not any(
        event.get("name") == "browser_progress"
        for event in _events(stream)
    )


def test_stop_notification_precedes_terminal_event():
    formatter = AGUIStreamEventFormatter(
        EventEncoder(),
        thread_id="thread-1",
        run_id="run-1",
    )

    stream = formatter.format_event("init")
    stream += formatter.format_event("stop")
    events = _events(stream)

    assert events[-2]["type"] == "CUSTOM"
    assert events[-2]["name"] == "stream_stopped"
    assert events[-1]["type"] == "RUN_FINISHED"


def test_reasoning_uses_standard_lifecycle():
    formatter = AGUIStreamEventFormatter(
        EventEncoder(),
        thread_id="thread-1",
        run_id="run-1",
    )

    stream = formatter.format_event("init")
    stream += formatter.format_event("reasoning", reasoning_text="first")
    stream += formatter.format_event("reasoning", reasoning_text="second")
    stream += formatter.format_event("response", text="answer")
    events = _events(stream)
    event_types = [event["type"] for event in events]

    assert event_types == [
        "RUN_STARTED",
        "REASONING_START",
        "REASONING_MESSAGE_START",
        "REASONING_MESSAGE_CONTENT",
        "REASONING_MESSAGE_CONTENT",
        "REASONING_MESSAGE_END",
        "REASONING_END",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
    ]


def test_interrupt_emits_legacy_event_then_standard_outcome():
    formatter = AGUIStreamEventFormatter(
        EventEncoder(),
        thread_id="thread-1",
        run_id="run-1",
    )

    stream = formatter.format_event("init")
    stream += formatter.format_event(
        "interrupt",
        interrupts=[{
            "id": "approval-1",
            "name": "approve-action",
            "reason": {"plan": "perform action"},
        }],
    )
    events = _events(stream)

    assert events[-2]["type"] == "CUSTOM"
    assert events[-2]["name"] == "interrupt"
    assert events[-1]["type"] == "RUN_FINISHED"
    assert events[-1]["outcome"]["type"] == "interrupt"
    assert events[-1]["outcome"]["interrupts"][0]["id"] == "approval-1"
