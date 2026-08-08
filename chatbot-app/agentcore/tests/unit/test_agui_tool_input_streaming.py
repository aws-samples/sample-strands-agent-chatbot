"""Tool calls should reach the AG-UI stream before their input JSON completes."""

import json

import pytest

from streaming.agui_event_processor import AGUIStreamEventProcessor


def _parse_sse_events(chunks: list[str]) -> list[dict]:
    events = []
    for chunk in chunks:
        for frame in chunk.split("\n\n"):
            for line in frame.splitlines():
                if line.startswith("data:"):
                    events.append(json.loads(line[len("data:"):].strip()))
    return events


class FakeAgent:
    def __init__(self, events):
        self.events = events

    async def stream_async(self, *_args, **_kwargs):
        for event in self.events:
            yield event


async def _collect(events):
    processor = AGUIStreamEventProcessor(thread_id="thread", run_id="run")
    processor._check_stop_signal = lambda: False
    chunks = [
        chunk
        async for chunk in processor.process_stream(
            FakeAgent(events),
            "hello",
        )
    ]
    return processor, _parse_sse_events(chunks)


@pytest.mark.asyncio
async def test_streams_start_args_and_end_as_separate_events():
    processor, events = await _collect([
        {
            "event": {
                "contentBlockStart": {
                    "start": {
                        "toolUse": {
                            "toolUseId": "tool-1",
                            "name": "search",
                        }
                    }
                }
            }
        },
        {
            "type": "tool_use_stream",
            "delta": {"toolUse": {"input": '{"query":'}},
            "current_tool_use": {
                "toolUseId": "tool-1",
                "name": "search",
                "input": '{"query":',
            },
        },
        {
            "type": "tool_use_stream",
            "delta": {"toolUse": {"input": '"mailbox"}'}},
            "current_tool_use": {
                "toolUseId": "tool-1",
                "name": "search",
                "input": '{"query":"mailbox"}',
            },
        },
        {"event": {"contentBlockStop": {"contentBlockIndex": 0}}},
    ])

    tool_events = [
        event for event in events
        if event["type"].startswith("TOOL_CALL")
    ]
    assert [event["type"] for event in tool_events] == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
    ]
    assert [event["delta"] for event in tool_events[1:3]] == [
        '{"query":',
        '"mailbox"}',
    ]
    assert processor.tool_use_registry["tool-1"]["input"] == {
        "query": "mailbox",
    }


@pytest.mark.asyncio
async def test_starts_skill_executor_with_inner_tool_name_before_input_completes():
    _, events = await _collect([
        {
            "event": {
                "contentBlockStart": {
                    "start": {
                        "toolUse": {
                            "toolUseId": "tool-2",
                            "name": "skill_executor",
                        }
                    }
                }
            }
        },
        {
            "type": "tool_use_stream",
            "delta": {
                "toolUse": {
                    "input": (
                        '{"skill_name":"web-search",'
                        '"tool_name":"tavily_search",'
                    )
                }
            },
            "current_tool_use": {
                "toolUseId": "tool-2",
                "name": "skill_executor",
                "input": (
                    '{"skill_name":"web-search",'
                    '"tool_name":"tavily_search",'
                ),
            },
        },
        {
            "type": "tool_use_stream",
            "delta": {"toolUse": {"input": '"tool_input":"{}"}'}},
            "current_tool_use": {
                "toolUseId": "tool-2",
                "name": "skill_executor",
                "input": (
                    '{"skill_name":"web-search",'
                    '"tool_name":"tavily_search",'
                    '"tool_input":"{}"}'
                ),
            },
        },
        {"event": {"contentBlockStop": {"contentBlockIndex": 0}}},
    ])

    tool_events = [
        event for event in events
        if event["type"].startswith("TOOL_CALL")
    ]
    assert [event["type"] for event in tool_events] == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
    ]
    assert tool_events[0]["toolCallName"] == "tavily_search"
    assert [event["delta"] for event in tool_events[1:3]] == [
        '{"skill_name":"web-search","tool_name":"tavily_search",',
        '"tool_input":"{}"}',
    ]
    assert not any(
        event["type"] == "CUSTOM"
        and event.get("name") == "tool_call_name_update"
        for event in events
    )


@pytest.mark.asyncio
async def test_starts_skill_script_with_script_name():
    _, events = await _collect([
        {
            "event": {
                "contentBlockStart": {
                    "start": {
                        "toolUse": {
                            "toolUseId": "tool-script",
                            "name": "skill_executor",
                        }
                    }
                }
            }
        },
        {
            "type": "tool_use_stream",
            "delta": {
                "toolUse": {
                    "input": (
                        '{"skill_name":"workspace",'
                        '"script_name":"cleanup.py",'
                    )
                }
            },
            "current_tool_use": {
                "toolUseId": "tool-script",
                "name": "skill_executor",
                "input": (
                    '{"skill_name":"workspace",'
                    '"script_name":"cleanup.py",'
                ),
            },
        },
        {
            "type": "tool_use_stream",
            "delta": {"toolUse": {"input": '"script_input":{}}'}},
            "current_tool_use": {
                "toolUseId": "tool-script",
                "name": "skill_executor",
                "input": (
                    '{"skill_name":"workspace",'
                    '"script_name":"cleanup.py",'
                    '"script_input":{}}'
                ),
            },
        },
        {"event": {"contentBlockStop": {"contentBlockIndex": 0}}},
    ])

    start = next(event for event in events if event["type"] == "TOOL_CALL_START")
    assert start["toolCallName"] == "cleanup.py"
