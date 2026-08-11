import asyncio
import hashlib
import json
import threading
from decimal import Decimal

import pytest

from agent import delegation_jobs


@pytest.fixture(autouse=True)
def local_storage(tmp_path, monkeypatch):
    monkeypatch.delenv("SESSION_ORCHESTRATION_TABLE", raising=False)
    monkeypatch.delenv("SESSION_MAILBOX_WRITE_ENABLED", raising=False)
    monkeypatch.setattr(delegation_jobs, "get_sessions_dir", lambda: tmp_path)


def _request(goal="Inspect the data"):
    return {
        "schemaVersion": 1,
        "goal": goal,
        "deliverable": "A concise report",
        "acceptanceCriteria": ["List anomalies"],
        "workspacePaths": ["uploads/events.jsonl"],
        "constraints": [],
        "contextSummary": "",
        "budget": {"maxSeconds": 60, "maxToolCalls": 20},
    }


def test_job_id_is_deterministic():
    assert delegation_jobs.job_id_for("session:run:tool") == (
        delegation_jobs.job_id_for("session:run:tool")
    )
    assert delegation_jobs.job_id_for("session:run:tool") != (
        delegation_jobs.job_id_for("session:run:other")
    )


def test_idempotent_start_returns_existing_job(monkeypatch):
    started = threading.Event()

    async def run_stub(_record, _factory):
        started.set()

    monkeypatch.setattr(delegation_jobs, "_run", run_stub)

    first = delegation_jobs.start_job(
        user_id="u1",
        session_id="s1",
        idempotency_key="s1:r1:t1",
        profile="analyst",
        request=_request(),
    )
    assert started.wait(timeout=1)

    second = delegation_jobs.start_job(
        user_id="u1",
        session_id="s1",
        idempotency_key="s1:r1:t1",
        profile="analyst",
        request=_request(),
    )
    assert second.job_id == first.job_id
    assert len(delegation_jobs.list_jobs("u1", "s1")) == 1


def test_idempotency_conflict_rejects_different_request(monkeypatch):
    async def run_stub(_record, _factory):
        return None

    monkeypatch.setattr(delegation_jobs, "_run", run_stub)

    delegation_jobs.start_job(
        user_id="u1",
        session_id="s1",
        idempotency_key="s1:r1:t1",
        profile="reviewer",
        request=_request(),
    )

    with pytest.raises(delegation_jobs.DelegationConflictError):
        delegation_jobs.start_job(
            user_id="u1",
            session_id="s1",
            idempotency_key="s1:r1:t1",
            profile="reviewer",
            request=_request("Review a different file"),
        )


def test_cancel_marks_non_terminal_job(monkeypatch):
    async def run_stub(_record, _factory):
        return None

    monkeypatch.setattr(delegation_jobs, "_run", run_stub)
    receipt = delegation_jobs.start_job(
        user_id="u1",
        session_id="s1",
        idempotency_key="s1:r1:t1",
        profile="analyst",
        request=_request(),
    )
    record = delegation_jobs.cancel_job("u1", "s1", receipt.job_id)
    assert record["desiredState"] == "cancelled"


def test_normalize_result_bounds_summary():
    result = delegation_jobs._normalize_result(
        json.dumps(
            {
                "summary": "x" * 9_000,
                "findings": ["one"],
                "artifacts": ["outputs/report.md"],
            }
        )
    )
    assert len(result["summary"]) < 8_100
    assert result["summary"].endswith("[Summary truncated]")
    assert result["findings"] == ["one"]


def test_event_factory_serializes_dynamodb_decimals(monkeypatch):
    captured = {}

    def send_stub(agent_name, message, session_id, region, **kwargs):
        captured.update(
            agent_name=agent_name,
            message=message,
            session_id=session_id,
            region=region,
            metadata=kwargs["metadata"],
        )

        async def events():
            if False:
                yield {}

        return events()

    monkeypatch.setattr("a2a_tools.send_a2a_message", send_stub)
    record = {
        "jobId": "job1",
        "userId": "u1",
        "sessionId": "s1",
        "profile": "analyst",
        "attempts": Decimal("1"),
        "modelId": "model1",
        "request": {
            **_request(),
            "schemaVersion": Decimal("1"),
            "budget": {
                "maxSeconds": Decimal("60"),
                "maxToolCalls": Decimal("20"),
            },
        },
    }

    delegation_jobs._event_factory(record)("job1")

    assert json.loads(captured["message"])["schemaVersion"] == 1
    assert captured["metadata"]["max_seconds"] == 60


def test_run_persists_failure():
    record = {
        "sessionKey": "USER#u1#SESSION#s1",
        "recordKey": "JOB#job1",
        "recordType": "DELEGATION_JOB",
        "jobId": "job1",
        "userId": "u1",
        "sessionId": "s1",
        "profile": "analyst",
        "executionStatus": "queued",
        "workStatus": "running",
        "deliveryStatus": "none",
        "desiredState": "running",
        "executionToken": "token-1",
        "attempts": 3,
        "request": _request(),
    }
    delegation_jobs._save(record)

    async def events():
        yield {
            "status": "error",
            "content": [{"text": "analysis failed"}],
        }

    asyncio.run(delegation_jobs._run(record, lambda _job_id: events()))
    saved = delegation_jobs.get_job("u1", "s1", "job1")
    assert saved["executionStatus"] == "failed"
    assert saved["error"] == "analysis failed"


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError, match="Unsupported delegation profile"):
        delegation_jobs.start_job(
            user_id="u1",
            session_id="s1",
            idempotency_key="s1:r1:t1",
            profile="general",
            request=_request(),
        )


def test_local_storage_rejects_path_traversal():
    with pytest.raises(ValueError, match="Invalid local delegation session ID"):
        delegation_jobs.get_job("u1", "../outside", "job1")

    with pytest.raises(ValueError, match="Invalid local delegation job ID"):
        delegation_jobs.get_job("u1", "s1", "../outside")


def test_local_storage_rejects_session_symlink_escape(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    storage_root = tmp_path / "delegation_jobs"
    storage_root.mkdir()
    session_key = hashlib.sha256(b"s1").hexdigest()
    (storage_root / session_key).symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(ValueError, match="cannot be a symlink"):
        delegation_jobs.get_job("u1", "s1", "job1")

    assert not (outside / "delegation_jobs").exists()


def test_list_jobs_ignores_symlinked_records(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-record.json"
    outside.write_text(json.dumps({
        "recordType": "DELEGATION_JOB",
        "userId": "u1",
        "sessionId": "s1",
    }))
    jobs_dir = delegation_jobs._local_dir("s1")
    (jobs_dir / "external.json").symlink_to(outside)

    assert delegation_jobs.list_jobs("u1", "s1") == []
