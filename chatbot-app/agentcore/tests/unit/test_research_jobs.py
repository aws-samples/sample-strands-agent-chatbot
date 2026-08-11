import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

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
    monkeypatch.setattr(research_jobs, "_mailbox_write_enabled", lambda: False)

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
    delivery_ready_index = next(
        i for i, item in enumerate(writes)
        if item[0] == "job" and item[1]["status"] == "delivering"
    )
    assert report_index < delivery_ready_index
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
    monkeypatch.setattr(research_jobs, "_mailbox_write_enabled", lambda: False)

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

    monkeypatch.setattr(
        research_jobs,
        "_create_job",
        lambda record: saved.append(deepcopy(record)),
    )
    monkeypatch.setattr(
        research_jobs,
        "start_job_execution",
        lambda *args, **kwargs: started.append((args, kwargs)),
    )

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
    assert saved[0]["workStatus"] == "queued"
    assert saved[0]["desiredState"] == "running"
    assert len(started) == 1


def test_cloud_job_create_uses_orchestration_table(monkeypatch):
    table = MagicMock()
    resource = MagicMock()
    resource.Table.return_value = table
    monkeypatch.setenv("DYNAMODB_USERS_TABLE", "users-table")
    monkeypatch.setenv("SESSION_ORCHESTRATION_TABLE", "orchestration-table")
    monkeypatch.setattr(research_jobs.boto3, "resource", lambda *args, **kwargs: resource)
    record = {
        "jobId": "job-1",
        "sessionId": "session-1",
        "userId": "user-1",
        "status": "queued",
    }

    research_jobs._create_job(record)

    resource.Table.assert_called_once_with("orchestration-table")
    item = table.put_item.call_args.kwargs["Item"]
    assert item["sessionKey"] == "USER#user-1#SESSION#session-1"
    assert item["recordKey"] == "JOB#job-1"
    assert item["recordType"] == "RESEARCH_JOB"
    assert "sk" not in item
    assert "attribute_not_exists" in table.put_item.call_args.kwargs[
        "ConditionExpression"
    ]


def test_cloud_job_reads_merge_orchestration_and_legacy_rows(monkeypatch):
    orchestration = MagicMock()
    orchestration.query.return_value = {
        "Items": [{
            "jobId": "new-job",
            "sessionId": "session-1",
            "userId": "user-1",
        }],
    }
    legacy = MagicMock()
    legacy.query.return_value = {
        "Items": [
            {
                "jobId": "new-job",
                "status": "stale",
            },
            {
                "jobId": "old-job",
                "sessionId": "session-1",
                "userId": "user-1",
            },
        ],
    }
    resource = MagicMock()
    resource.Table.side_effect = lambda name: (
        orchestration if name == "orchestration-table" else legacy
    )
    monkeypatch.setenv("DYNAMODB_USERS_TABLE", "users-table")
    monkeypatch.setenv("SESSION_ORCHESTRATION_TABLE", "orchestration-table")
    monkeypatch.setenv("RESEARCH_JOB_LEGACY_READ_ENABLED", "true")
    monkeypatch.setattr(research_jobs.boto3, "resource", lambda *args, **kwargs: resource)

    jobs = research_jobs._list_jobs("user-1", "session-1")

    assert {item["jobId"] for item in jobs} == {"new-job", "old-job"}
    assert next(item for item in jobs if item["jobId"] == "new-job").get("status") != (
        "stale"
    )
    assert orchestration.query.call_args.kwargs["ExpressionAttributeValues"] == {
        ":session_key": "USER#user-1#SESSION#session-1",
        ":prefix": "JOB#",
    }


def test_cloud_job_reads_skip_legacy_table_by_default(monkeypatch):
    orchestration = MagicMock()
    orchestration.query.return_value = {
        "Items": [{
            "jobId": "new-job",
            "sessionId": "session-1",
            "userId": "user-1",
        }],
    }
    legacy = MagicMock()
    resource = MagicMock()
    resource.Table.side_effect = lambda name: (
        orchestration if name == "orchestration-table" else legacy
    )
    monkeypatch.setenv("DYNAMODB_USERS_TABLE", "users-table")
    monkeypatch.setenv("SESSION_ORCHESTRATION_TABLE", "orchestration-table")
    monkeypatch.delenv("RESEARCH_JOB_LEGACY_READ_ENABLED", raising=False)
    monkeypatch.setattr(research_jobs.boto3, "resource", lambda *args, **kwargs: resource)

    jobs = research_jobs._list_jobs("user-1", "session-1")

    assert [item["jobId"] for item in jobs] == ["new-job"]
    legacy.query.assert_not_called()


def test_completion_mailbox_event_is_small_and_idempotent(monkeypatch):
    observed = []

    class Repository:
        def enqueue(self, event):
            from agent.mailbox import DynamoDBMailboxRepository

            DynamoDBMailboxRepository(
                "mailbox-table",
                client=MagicMock(),
            )._serialize(event.to_record())
            observed.append(event)
            return len(observed) == 1

    monkeypatch.setattr(research_jobs, "_mailbox_write_enabled", lambda: True)
    monkeypatch.setattr(
        "agent.mailbox.get_mailbox_repository",
        lambda: Repository(),
    )
    record = {
        "jobId": "a" * 32,
        "sessionId": "session-1",
        "userId": "user-1",
        "artifactId": "research-tool-1",
        "artifact": {
            "id": "research-tool-1",
            "title": "Report",
            "type": "research",
            "metadata": {
                "word_count": Decimal("527"),
            },
        },
        "artifactBucket": "bucket",
        "artifactS3Key": "research/report.md",
    }

    first = research_jobs._enqueue_completion_event(record)
    second = research_jobs._enqueue_completion_event(record)

    assert first == second == f"research-result:{record['jobId']}"
    assert observed[0].event_type == "async_result.ready"
    assert observed[0].payload_ref == {
        "bucket": "bucket",
        "key": "research/report.md",
    }
    assert "content" not in observed[0].payload["artifact"]


def test_artifact_state_reference_excludes_report_body():
    artifact = {
        "id": "artifact-1",
        "title": "Report",
        "content": "# Large report",
    }

    reference = research_jobs._build_artifact_reference(
        {
            "artifactBucket": "bucket",
            "artifactS3Key": "reports/job-1.md",
        },
        artifact,
    )

    assert reference == {
        "id": "artifact-1",
        "title": "Report",
        "content_ref": {
            "bucket": "bucket",
            "key": "reports/job-1.md",
        },
    }


def test_recover_pending_job_creates_deterministic_mailbox_event(monkeypatch):
    updated = []
    record = {
        "jobId": "job-1",
        "sessionId": "session-1",
        "userId": "user-1",
        "status": "completed",
    }
    monkeypatch.setattr(
        research_jobs,
        "load_pending_results",
        lambda user_id, session_id: [{"record": record, "artifact": {}}],
    )
    monkeypatch.setattr(
        research_jobs,
        "_enqueue_completion_event",
        lambda item: "research-result:job-1",
    )
    monkeypatch.setattr(
        research_jobs,
        "_update_existing",
        lambda item, changes, **kwargs: updated.append(
            (deepcopy(changes), kwargs)
        ) or True,
    )

    recovered = research_jobs.recover_pending_mailbox_events(
        "user-1",
        "session-1",
    )

    assert recovered == ["research-result:job-1"]
    assert updated[0][0]["mailboxEventId"] == "research-result:job-1"
    assert updated[0][0]["status"] == "delivering"
    assert updated[0][0]["workStatus"] == "terminal"
    assert updated[0][1]["allowed_statuses"] == ("completed", "delivering")


def test_recover_pending_job_reenqueues_existing_idempotency_key(monkeypatch):
    record = {
        "jobId": "job-1",
        "userId": "user-1",
        "sessionId": "session-1",
        "status": "delivering",
        "mailboxEventId": "research-result:job-1",
    }
    monkeypatch.setattr(
        research_jobs,
        "load_pending_results",
        lambda user_id, session_id: [{"record": record, "artifact": {}}],
    )
    enqueue = MagicMock(return_value="research-result:job-1")
    update = MagicMock(return_value=True)
    monkeypatch.setattr(research_jobs, "_enqueue_completion_event", enqueue)
    monkeypatch.setattr(research_jobs, "_update_existing", update)

    recovered = research_jobs.recover_pending_mailbox_events(
        "user-1",
        "session-1",
    )

    assert recovered == ["research-result:job-1"]
    enqueue.assert_called_once_with(record)
    update.assert_called_once()


@pytest.mark.parametrize("job_status", ["completed", "delivering"])
def test_reconcile_processed_delivery_marks_job_delivered(monkeypatch, job_status):
    from agent.mailbox import MailboxEvent, PROCESSED

    record = {
        "jobId": "job-1",
        "userId": "user-1",
        "sessionId": "session-1",
        "status": job_status,
        "mailboxEventId": "research-result:job-1",
    }
    event = replace(
        MailboxEvent.create(
            event_id="research-result:job-1",
            event_type="async_result.ready",
            session_id="session-1",
            user_id="user-1",
            source_type="research_job",
            source_id="job-1",
        ),
        status=PROCESSED,
    )
    repository = MagicMock()
    repository.get_event.return_value = event
    delivered = MagicMock()
    monkeypatch.setattr(research_jobs, "_list_jobs", lambda *_: [record])
    monkeypatch.setattr(research_jobs, "mark_delivered", delivered)
    monkeypatch.setattr(
        "agent.mailbox.get_mailbox_repository",
        lambda: repository,
    )

    reconciled = research_jobs.reconcile_processed_deliveries(
        "user-1",
        "session-1",
    )

    assert reconciled == 1
    delivered.assert_called_once_with(record)


@pytest.mark.asyncio
async def test_mailbox_delivery_notifies_instead_of_direct_callback(monkeypatch):
    writes = []
    enqueue_snapshots = []
    direct_deliveries = []
    notifications = []

    monkeypatch.setattr(
        research_jobs,
        "_save_job",
        lambda record: writes.append(deepcopy(record)),
    )
    monkeypatch.setattr(
        research_jobs,
        "_save_report",
        lambda record, report: {
            "artifactPath": "/tmp/report.md",
        },
    )
    monkeypatch.setattr(
        research_jobs,
        "_enqueue_completion_event",
        lambda record: (
            enqueue_snapshots.append(deepcopy(record))
            or "research-result:job-1"
        ),
    )
    monkeypatch.setattr(research_jobs, "_mailbox_write_enabled", lambda: True)
    monkeypatch.setattr(research_jobs.async_tasks, "begin", lambda *args, **kwargs: 1)
    monkeypatch.setattr(research_jobs.async_tasks, "end", lambda task_id: True)
    monkeypatch.setattr(
        "agent.mailbox_runtime.mailbox_delivery_enabled",
        lambda: True,
    )

    async def direct_delivery(record, artifact):
        direct_deliveries.append(record)

    async def notify(record):
        notifications.append(record["mailboxEventId"])
        return type("Result", (), {"dead": 0})()

    monkeypatch.setattr(research_jobs, "_deliver", direct_delivery)
    monkeypatch.setattr(research_jobs, "_notify_mailbox", notify)

    async def events():
        yield {"status": "success", "content": [{"text": "# Report"}]}

    record = {
        "jobId": "job-1",
        "sessionId": "session-1",
        "userId": "user-1",
        "artifactId": "research-tool-1",
        "plan": "plan",
        "status": "queued",
        "createdAt": "now",
        "updatedAt": "now",
    }
    await research_jobs._run_job(record, events)

    assert notifications == ["research-result:job-1"]
    assert direct_deliveries == []
    assert writes[-1]["status"] == "delivering"
    assert enqueue_snapshots[0]["status"] == "delivering"
    assert enqueue_snapshots[0]["mailboxEventId"] == "research-result:job-1"
    assert writes.index(next(
        item for item in writes
        if item.get("mailboxEventId") == "research-result:job-1"
    )) == len(writes) - 1


@pytest.mark.asyncio
async def test_deleted_session_cancels_completed_research_delivery(monkeypatch):
    from agent.mailbox import SessionDeletedError

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
    monkeypatch.setattr(
        research_jobs,
        "_enqueue_completion_event",
        MagicMock(side_effect=SessionDeletedError("deleted")),
    )
    monkeypatch.setattr(research_jobs.async_tasks, "begin", lambda *args, **kwargs: 1)
    monkeypatch.setattr(research_jobs.async_tasks, "end", lambda task_id: True)
    delivery = AsyncMock()
    monkeypatch.setattr(research_jobs, "_deliver", delivery)

    async def events():
        yield {"status": "success", "content": [{"text": "# Report"}]}

    record = {
        "jobId": "job-1",
        "sessionId": "session-1",
        "userId": "user-1",
        "artifactId": "artifact-1",
        "status": "queued",
        "createdAt": "now",
        "updatedAt": "now",
    }
    await research_jobs._run_job(record, events)

    assert writes[-1]["status"] == "cancelled"
    delivery.assert_not_awaited()


@pytest.mark.asyncio
async def test_mailbox_write_failure_stays_retryable_without_direct_delivery(
    monkeypatch,
):
    writes = []
    deliveries = []
    notifications = []
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
    monkeypatch.setattr(research_jobs, "_mailbox_write_enabled", lambda: True)
    monkeypatch.setattr(
        research_jobs,
        "_enqueue_completion_event",
        MagicMock(side_effect=RuntimeError("write failed")),
    )
    monkeypatch.setattr(
        research_jobs,
        "_completion_event_exists",
        lambda record: False,
    )
    monkeypatch.setattr(research_jobs.async_tasks, "begin", lambda *args, **kwargs: 1)
    monkeypatch.setattr(research_jobs.async_tasks, "end", lambda task_id: True)
    monkeypatch.setattr(
        "agent.mailbox_runtime.mailbox_delivery_enabled",
        lambda: True,
    )

    async def direct_delivery(record, artifact):
        deliveries.append(record["jobId"])

    async def notify(record):
        notifications.append(record["jobId"])

    monkeypatch.setattr(research_jobs, "_deliver", direct_delivery)
    monkeypatch.setattr(research_jobs, "_notify_mailbox", notify)

    async def events():
        yield {"status": "success", "content": [{"text": "# Report"}]}

    record = {
        "jobId": "job-1",
        "sessionId": "session-1",
        "userId": "user-1",
        "artifactId": "artifact-1",
        "status": "queued",
        "createdAt": "now",
        "updatedAt": "now",
    }
    await research_jobs._run_job(record, events)

    assert notifications == []
    assert deliveries == []
    assert writes[-1]["status"] == "completed"
    assert writes[-1]["deliveryError"] == (
        "Mailbox delivery is enabled but completion enqueue failed"
    )
    assert writes[-1]["mailboxEventId"] == "research-result:job-1"
    assert writes[-1]["mailboxWriteError"] == "write failed"


def test_cloud_claim_supports_stale_takeover(monkeypatch):
    table = MagicMock()
    current = {
        "jobId": "job-1",
        "userId": "user-1",
        "sessionId": "session-1",
        "status": "running",
        "workStatus": "running",
        "desiredState": "running",
        "heartbeatAt": "2020-01-01T00:00:00+00:00",
        "attempts": 1,
    }
    table.update_item.return_value = {
        "Attributes": {
            **current,
            "executionToken": "new-token",
            "attempts": 2,
        }
    }
    monkeypatch.setenv("DYNAMODB_USERS_TABLE", "users-table")
    monkeypatch.setenv("SESSION_ORCHESTRATION_TABLE", "orchestration-table")
    monkeypatch.setattr(research_jobs, "_orchestration_table", lambda: table)
    monkeypatch.setattr(research_jobs, "_get_job", lambda *_: current)

    claimed = research_jobs._claim_job("user-1", "session-1", "job-1")

    assert claimed["executionToken"] == "new-token"
    condition = table.update_item.call_args.kwargs["ConditionExpression"]
    assert "heartbeatAt < :stale" in condition
    assert "attempts < :maxAttempts" in condition


def test_owned_update_rejects_cancelled_or_taken_over_job(monkeypatch):
    table = MagicMock()
    error = RuntimeError("condition failed")
    error.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
    table.update_item.side_effect = error
    monkeypatch.setenv("DYNAMODB_USERS_TABLE", "users-table")
    monkeypatch.setenv("SESSION_ORCHESTRATION_TABLE", "orchestration-table")
    monkeypatch.setattr(research_jobs, "_orchestration_table", lambda: table)

    updated = research_jobs._update_owned(
        {
            "jobId": "job-1",
            "userId": "user-1",
            "sessionId": "session-1",
            "executionToken": "old-token",
        },
        {"status": "delivering"},
    )

    assert updated is False


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
