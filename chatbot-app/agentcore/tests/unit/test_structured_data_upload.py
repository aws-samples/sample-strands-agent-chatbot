"""Structured JSON/JSONL attachment handling."""

import base64
from types import SimpleNamespace

from agent.processor.multimodal_builder import (
    _STRUCTURED_TEXT_MAX_CHARS,
    build_prompt,
)


def _file(name: str, content_type: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(
        filename=name,
        content_type=content_type,
        bytes=base64.b64encode(content.encode("utf-8")).decode("ascii"),
    )


def test_json_is_sent_as_text_with_full_workspace_path():
    prompt, uploaded = build_prompt(
        "Analyze this data.",
        files=[_file("records.json", "application/json", '{"items":[1,2]}')],
        auto_store=False,
    )

    assert prompt[0]["text"].startswith("Analyze this data.")
    structured = prompt[1]["text"]
    assert '<structured_data name="records.json"' in structured
    assert 'workspace_path="/mnt/workspace/inputs/records.json"' in structured
    assert 'truncated="false"' in structured
    assert '{"items":[1,2]}' in structured
    assert uploaded[0]["filename"] == "records.json"


def test_jsonl_text_is_truncated_without_truncating_workspace_bytes():
    content = '{"value":"' + ("x" * (_STRUCTURED_TEXT_MAX_CHARS + 100)) + '"}\n'
    prompt, uploaded = build_prompt(
        "Summarize.",
        files=[_file("records.jsonl", "application/x-ndjson", content)],
        auto_store=False,
    )

    structured = prompt[1]["text"]
    assert 'truncated="true"' in structured
    assert "... [truncated," in structured
    assert len(uploaded[0]["bytes"]) == len(content.encode("utf-8"))
    assert uploaded[0]["bytes"].endswith(b'"}\n')
