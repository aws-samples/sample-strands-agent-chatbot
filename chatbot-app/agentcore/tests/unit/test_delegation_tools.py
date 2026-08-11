import json
from types import SimpleNamespace

import pytest

from local_tools import delegation


class FakeState(dict):
    pass


def _context():
    return SimpleNamespace(
        invocation_state=FakeState(
            session_id="session-1",
            user_id="user-1",
            run_id="run-1",
            model_id="model-1",
            auth_token="token",
        ),
        tool_use={"toolUseId": "tool-1"},
    )


def test_delegate_task_builds_deterministic_scoped_request(monkeypatch):
    captured = {}

    def start_stub(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            as_dict=lambda: {
                "job_id": "job-1",
                "status": "queued",
                "profile": "analyst",
            }
        )

    monkeypatch.setattr(delegation, "start_job", start_stub)
    result = delegation.delegate_task(
        profile="analyst",
        goal="Inspect the JSONL",
        deliverable="An anomaly report",
        acceptance_criteria=["List invalid lines"],
        workspace_paths=["uploads/events.jsonl"],
        constraints=["Do not modify source"],
        tool_context=_context(),
    )

    receipt = json.loads(result)
    assert receipt["status"] == "accepted"
    assert receipt["profile"] == "analyst"
    assert "job_id" not in receipt
    assert "job-1" not in result
    assert captured["idempotency_key"] == "session-1:run-1:tool-1"
    assert captured["request"]["workspacePaths"] == ["uploads/events.jsonl"]
    assert captured["request"]["constraints"] == ["Do not modify source"]


def test_delegate_task_rejects_broad_profile():
    with pytest.raises(ValueError, match="profile must be"):
        delegation.delegate_task(
            profile="general",
            goal="Do everything",
            deliverable="Everything",
            tool_context=_context(),
        )


def test_delegate_task_limits_budget():
    with pytest.raises(ValueError, match="max_seconds"):
        delegation.delegate_task(
            profile="reviewer",
            goal="Review",
            deliverable="Report",
            max_seconds=10,
            tool_context=_context(),
        )


def test_get_delegation_filters_without_exposing_job_id(monkeypatch):
    monkeypatch.setattr(
        delegation,
        "list_jobs",
        lambda user_id, session_id: [{
            "jobId": "internal-job-1",
            "profile": "analyst",
            "executionStatus": "running",
            "deliveryStatus": "none",
            "createdAt": "2026-08-10T00:00:00Z",
            "request": {
                "goal": "Inspect event anomalies",
                "deliverable": "An anomaly report",
            },
        }],
    )

    result = delegation.get_delegation(
        profile="analyst",
        goal_contains="event",
        tool_context=_context(),
    )
    payload = json.loads(result)

    assert payload["status"] == "ok"
    assert payload["delegations"][0]["goal"] == "Inspect event anomalies"
    assert "jobId" not in result
    assert "internal-job-1" not in result


def test_cancel_delegation_resolves_single_match_internally(monkeypatch):
    record = {
        "jobId": "internal-job-1",
        "profile": "reviewer",
        "executionStatus": "running",
        "deliveryStatus": "none",
        "createdAt": "2026-08-10T00:00:00Z",
        "request": {
            "goal": "Review the schema",
            "deliverable": "A review",
        },
    }
    cancelled_ids = []
    monkeypatch.setattr(
        delegation,
        "list_jobs",
        lambda user_id, session_id: [record],
    )
    monkeypatch.setattr(
        delegation,
        "cancel_job",
        lambda user_id, session_id, job_id: (
            cancelled_ids.append(job_id)
            or {**record, "executionStatus": "cancelled"}
        ),
    )

    result = delegation.cancel_delegation(
        profile="reviewer",
        goal_contains="schema",
        tool_context=_context(),
    )

    assert cancelled_ids == ["internal-job-1"]
    assert json.loads(result)["status"] == "cancelled"
    assert "internal-job-1" not in result


def test_cancel_delegation_does_not_guess_between_matches(monkeypatch):
    monkeypatch.setattr(
        delegation,
        "list_jobs",
        lambda user_id, session_id: [
            {
                "jobId": f"internal-job-{index}",
                "profile": "analyst",
                "executionStatus": "running",
                "deliveryStatus": "none",
                "createdAt": f"2026-08-10T00:00:0{index}Z",
                "request": {
                    "goal": f"Analyze dataset {index}",
                    "deliverable": "A report",
                },
            }
            for index in (1, 2)
        ],
    )

    result = delegation.cancel_delegation(
        profile="analyst",
        tool_context=_context(),
    )

    payload = json.loads(result)
    assert payload["status"] == "ambiguous"
    assert len(payload["delegations"]) == 2
    assert "internal-job-" not in result
