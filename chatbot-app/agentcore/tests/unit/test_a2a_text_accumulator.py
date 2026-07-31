"""Regression tests for cumulative A2A task snapshot handling."""

from a2a_response import A2ATextAccumulator


def test_duplicate_snapshot_text_is_emitted_once():
    response = A2ATextAccumulator()

    response.add("Task completed")
    response.add("Task completed")
    response.add("  Task completed\n")

    assert response.text == "Task completed"


def test_distinct_summary_and_artifact_are_preserved():
    response = A2ATextAccumulator()

    response.add("Research completed")
    response.add("<research>\n# Report\n</research>")
    response.add("Research completed")
    response.add("<research>\n# Report\n</research>")

    assert response.text == (
        "Research completed\n\n"
        "<research>\n# Report\n</research>"
    )


def test_empty_chunks_are_ignored():
    response = A2ATextAccumulator()

    response.add(None)
    response.add("")
    response.add(" \n ")

    assert response.text == ""
