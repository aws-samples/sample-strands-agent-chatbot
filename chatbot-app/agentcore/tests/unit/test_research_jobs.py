import asyncio
from copy import deepcopy

import pytest

from agent import research_jobs


@pytest.fixture(autouse=True)
def clear_handler():
    research_jobs.clear_delivery_handler()
    yield
    research_jobs.clear_delivery_handler()


@pytest.mark.asyncio
async def test_report_is_durable_before_delivery(monkeypatch):
    writes = []
    deliveries = []

    monkeypatch.setattr(
        research_jobs,
        "_save_job",
        lambda record: writes.append(("job", deepcopy(record))),
    )
    monkeypatch.setattr(
        research_jobs,
        "_save_report",
        lambda record, report: writes.append(("report", report)) or {
            "artifactPath": "/tmp/report.md",
        },
    )
    monkeypatch.setattr(
        research_jobs.async_tasks,
        "begin",
        lambda *args, **kwargs: 1,
    )
    monkeypatch.setattr(research_jobs.async_tasks, "end", lambda task_id: True)

    async def deliver(record, artifact):
        deliveries.append((deepcopy(record), deepcopy(artifact)))

    monkeypatch.setattr(research_jobs, "_deliver", deliver)

    async def events():
        yield {"type": "research_step", "stepNumber": 1, "content": "Searching"}
        yield {"status": "success", "content": [{"text": "# Report\n\nDone."}]}

    record = {
        "jobId": "a" * 32,
        "sessionId": "session-1",
        "userId": "user-1",
        "artifactId": "research-tool-1",
        "plan": "plan",
        "status": "queued",
        "createdAt": "now",
        "updatedAt": "now",
    }
    await research_jobs._run_job(record, events)

    report_index = next(i for i, item in enumerate(writes) if item[0] == "report")
    completed_index = next(
        i for i, item in enumerate(writes)
        if item[0] == "job" and item[1]["status"] == "completed"
    )
    assert report_index < completed_index
    assert deliveries[0][1]["id"] == "research-tool-1"
    assert writes[-1][1]["status"] == "delivered"


@pytest.mark.asyncio
async def test_delivery_failure_keeps_completed_job_retryable(monkeypatch):
    writes = []
    monkeypatch.setattr(
        research_jobs,
        "_save_job",
        lambda record: writes.append(deepcopy(record)),
    )
    monkeypatch.setattr(
        research_jobs,
        "_save_report",
        lambda record, report: {"artifactPath": "/tmp/report.md"},
    )
    monkeypatch.setattr(research_jobs.async_tasks, "begin", lambda *args, **kwargs: 1)
    monkeypatch.setattr(research_jobs.async_tasks, "end", lambda task_id: True)

    async def fail_delivery(record, artifact):
        raise RuntimeError("delivery unavailable")

    monkeypatch.setattr(research_jobs, "_deliver", fail_delivery)

    async def events():
        yield {"status": "success", "content": [{"text": "# Report"}]}

    record = {
        "jobId": "b" * 32,
        "sessionId": "session-1",
        "userId": "user-1",
        "artifactId": "research-tool-2",
        "plan": "plan",
        "status": "queued",
        "createdAt": "now",
        "updatedAt": "now",
    }
    await research_jobs._run_job(record, events)

    assert writes[-1]["status"] == "completed"
    assert writes[-1]["deliveryError"] == "delivery unavailable"


def test_start_returns_without_running_worker_inline(monkeypatch):
    saved = []
    started = []

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self):
            started.append(self)

    monkeypatch.setattr(research_jobs, "_save_job", lambda record: saved.append(record))
    monkeypatch.setattr(research_jobs.threading, "Thread", FakeThread)

    async def events():
        yield {"status": "success", "content": [{"text": "# Report"}]}

    receipt = research_jobs.start_research_job(
        session_id="session-1",
        user_id="user-1",
        plan="plan",
        artifact_id="research-tool-3",
        event_factory=events,
    )

    assert receipt["status"] == "started"
    assert receipt["artifact_id"] == "research-tool-3"
    assert saved[0]["status"] == "queued"
    assert len(started) == 1
    assert started[0].daemon is True


@pytest.mark.asyncio
async def test_delivery_handler_runs_on_registered_loop(monkeypatch):
    observed = []

    async def handler(record, artifact):
        observed.append((record["jobId"], artifact["id"]))

    loop = asyncio.get_running_loop()
    research_jobs.register_delivery_handler(loop, handler)
    await research_jobs._deliver(
        {"jobId": "job-1"},
        {"id": "artifact-1"},
    )

    assert observed == [("job-1", "artifact-1")]
