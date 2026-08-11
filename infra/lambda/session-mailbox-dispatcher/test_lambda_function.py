import json
from unittest.mock import MagicMock

import lambda_function


def record(
    event_id: str,
    *,
    mailbox_event_id: str | None = None,
    user_id: str = "user-1",
    session_id: str = "session-1",
    event_name: str = "INSERT",
    record_type: str = "INBOX",
):
    return {
        "eventID": event_id,
        "eventName": event_name,
        "dynamodb": {
            "NewImage": {
                "recordType": {"S": record_type},
                "status": {"S": "pending"},
                "userId": {"S": user_id},
                "sessionId": {"S": session_id},
                "eventId": {"S": mailbox_event_id or f"mailbox-{event_id}"},
            }
        },
    }


def sqs_record(
    message_id: str,
    *,
    user_id: str = "user-1",
    session_id: str = "session-1",
    event_ids: list[str] | None = None,
):
    return {
        "messageId": message_id,
        "eventSource": "aws:sqs",
        "receiptHandle": f"receipt-{message_id}",
        "attributes": {"ApproximateReceiveCount": "1"},
        "body": json.dumps({
            "userId": user_id,
            "sessionId": session_id,
            "eventIds": event_ids or [f"event-{message_id}"],
        }),
    }


def delegation_record(
    event_id: str,
    *,
    job_id: str = "job-1",
    user_id: str = "user-1",
    session_id: str = "session-1",
):
    return {
        "eventID": event_id,
        "eventName": "INSERT",
        "dynamodb": {
            "NewImage": {
                "recordType": {"S": "DELEGATION_JOB"},
                "workStatus": {"S": "queued"},
                "desiredState": {"S": "running"},
                "userId": {"S": user_id},
                "sessionId": {"S": session_id},
                "jobId": {"S": job_id},
            }
        },
    }


def delegation_sqs_record(
    message_id: str,
    *,
    job_id: str = "job-1",
):
    item = sqs_record(message_id)
    item["body"] = json.dumps({
        "kind": "delegation",
        "userId": "user-1",
        "sessionId": "session-1",
        "jobId": job_id,
    })
    return item


def research_record(
    event_id: str,
    *,
    job_id: str = "research-1",
):
    item = delegation_record(event_id, job_id=job_id)
    item["dynamodb"]["NewImage"]["recordType"] = {"S": "RESEARCH_JOB"}
    return item


def research_sqs_record(
    message_id: str,
    *,
    job_id: str = "research-1",
):
    item = sqs_record(message_id)
    item["body"] = json.dumps({
        "kind": "research",
        "userId": "user-1",
        "sessionId": "session-1",
        "jobId": job_id,
    })
    return item


def test_coalesces_multiple_inserts_for_one_session(monkeypatch):
    enqueue = MagicMock()
    monkeypatch.setattr(lambda_function, "_enqueue_wake", enqueue)

    result = lambda_function.lambda_handler(
        {"Records": [record("1"), record("2")]},
        None,
    )

    assert result == {"batchItemFailures": []}
    enqueue.assert_called_once_with(
        "user-1",
        "session-1",
        ["mailbox-1", "mailbox-2"],
    )


def test_ignores_non_inbox_stream_records(monkeypatch):
    enqueue = MagicMock()
    monkeypatch.setattr(lambda_function, "_enqueue_wake", enqueue)

    result = lambda_function.lambda_handler(
        {
            "Records": [
                record("1", event_name="MODIFY"),
                record("2", record_type="JOB"),
            ]
        },
        None,
    )

    assert result == {"batchItemFailures": []}
    enqueue.assert_not_called()


def test_reports_each_coalesced_record_when_wake_fails(monkeypatch):
    monkeypatch.setattr(
        lambda_function,
        "_enqueue_wake",
        MagicMock(side_effect=RuntimeError("still pending")),
    )

    result = lambda_function.lambda_handler(
        {"Records": [record("1"), record("2")]},
        None,
    )

    assert result == {
        "batchItemFailures": [
            {"itemIdentifier": "1"},
            {"itemIdentifier": "2"},
        ]
    }


def test_stream_enqueues_delegation_job(monkeypatch):
    enqueue = MagicMock()
    monkeypatch.setattr(lambda_function, "_enqueue_delegation", enqueue)

    result = lambda_function.lambda_handler(
        {"Records": [delegation_record("stream-1")]},
        None,
    )

    assert result == {"batchItemFailures": []}
    enqueue.assert_called_once_with("user-1", "session-1", "job-1")


def test_sqs_worker_starts_delegation(monkeypatch):
    start = MagicMock()
    monkeypatch.setattr(lambda_function, "_start_delegation", start)

    result = lambda_function.lambda_handler(
        {"Records": [delegation_sqs_record("message-1")]},
        None,
    )

    assert result == {"batchItemFailures": []}
    start.assert_called_once_with("user-1", "session-1", "job-1")


def test_stream_enqueues_research_job(monkeypatch):
    enqueue = MagicMock()
    monkeypatch.setattr(lambda_function, "_enqueue_research", enqueue)

    result = lambda_function.lambda_handler(
        {"Records": [research_record("stream-1")]},
        None,
    )

    assert result == {"batchItemFailures": []}
    enqueue.assert_called_once_with("user-1", "session-1", "research-1")


def test_sqs_worker_starts_research(monkeypatch):
    start = MagicMock()
    monkeypatch.setattr(lambda_function, "_start_research", start)

    result = lambda_function.lambda_handler(
        {"Records": [research_sqs_record("message-1")]},
        None,
    )

    assert result == {"batchItemFailures": []}
    start.assert_called_once_with("user-1", "session-1", "research-1")


def test_reconcile_enqueues_queued_and_stale_jobs(monkeypatch):
    query = MagicMock(side_effect=[
        {
            "Items": [{
                "recordType": {"S": "DELEGATION_JOB"},
                "userId": {"S": "user-1"},
                "sessionId": {"S": "session-1"},
                "jobId": {"S": "queued-job"},
            }]
        },
        {
            "Items": [{
                "recordType": {"S": "RESEARCH_JOB"},
                "userId": {"S": "user-1"},
                "sessionId": {"S": "session-1"},
                "jobId": {"S": "stale-job"},
            }]
        },
    ])
    monkeypatch.setattr(
        lambda_function.boto3,
        "client",
        lambda service: MagicMock(query=query),
    )
    delegation = MagicMock()
    research = MagicMock()
    monkeypatch.setattr(lambda_function, "_enqueue_delegation", delegation)
    monkeypatch.setattr(lambda_function, "_enqueue_research", research)
    monkeypatch.setenv("ORCHESTRATION_TABLE_NAME", "orchestration")

    result = lambda_function.lambda_handler(
        {"source": "aws.events"},
        None,
    )

    assert result == {"status": "reconciled", "enqueued": 2}
    delegation.assert_called_once_with("user-1", "session-1", "queued-job")
    research.assert_called_once_with("user-1", "session-1", "stale-job")


def test_sqs_worker_coalesces_wakes_for_one_session(monkeypatch):
    wake = MagicMock()
    monkeypatch.setattr(
        lambda_function,
        "_discard_deleted_session_wake",
        MagicMock(return_value=False),
    )
    monkeypatch.setattr(lambda_function, "_wake", wake)

    result = lambda_function.lambda_handler(
        {"Records": [sqs_record("1"), sqs_record("2")]},
        None,
    )

    assert result == {"batchItemFailures": []}
    wake.assert_called_once_with(
        "user-1",
        "session-1",
        ["event-1", "event-2"],
    )


def test_sqs_worker_reports_failed_messages(monkeypatch):
    defer = MagicMock()
    monkeypatch.setattr(
        lambda_function,
        "_discard_deleted_session_wake",
        MagicMock(return_value=False),
    )
    monkeypatch.setattr(lambda_function, "_defer_retry", defer)
    monkeypatch.setattr(
        lambda_function,
        "_wake",
        MagicMock(side_effect=RuntimeError("still pending")),
    )

    result = lambda_function.lambda_handler(
        {"Records": [sqs_record("1"), sqs_record("2")]},
        None,
    )

    assert result == {
        "batchItemFailures": [
            {"itemIdentifier": "1"},
            {"itemIdentifier": "2"},
        ]
    }
    assert [call.args[0]["messageId"] for call in defer.call_args_list] == [
        "1",
        "2",
    ]


def test_sqs_worker_discards_deleted_session_wake(monkeypatch):
    discard = MagicMock(return_value=True)
    wake = MagicMock()
    monkeypatch.setattr(
        lambda_function,
        "_discard_deleted_session_wake",
        discard,
    )
    monkeypatch.setattr(lambda_function, "_wake", wake)

    result = lambda_function.lambda_handler(
        {"Records": [sqs_record("1")]},
        None,
    )

    assert result == {"batchItemFailures": []}
    discard.assert_called_once_with(
        "user-1",
        "session-1",
        ["event-1"],
    )
    wake.assert_not_called()


def test_enqueue_uses_session_group_and_event_deduplication(monkeypatch):
    send_message = MagicMock()
    sqs = MagicMock(send_message=send_message)
    monkeypatch.setattr(lambda_function.boto3, "client", lambda service: sqs)
    monkeypatch.setenv("WAKE_QUEUE_URL", "https://sqs.example/queue")

    lambda_function._enqueue_wake(
        "user-1",
        "session-1",
        ["event-2", "event-1"],
    )

    kwargs = send_message.call_args.kwargs
    assert kwargs["QueueUrl"] == "https://sqs.example/queue"
    assert json.loads(kwargs["MessageBody"]) == {
        "userId": "user-1",
        "sessionId": "session-1",
        "eventIds": ["event-2", "event-1"],
    }
    assert len(kwargs["MessageGroupId"]) == 64
    assert len(kwargs["MessageDeduplicationId"]) == 64


def test_failed_wake_uses_bounded_exponential_visibility(monkeypatch):
    change_visibility = MagicMock()
    sqs = MagicMock(change_message_visibility=change_visibility)
    monkeypatch.setattr(lambda_function.boto3, "client", lambda service: sqs)
    monkeypatch.setenv("WAKE_QUEUE_URL", "https://sqs.example/queue")
    failed = sqs_record("message-1")
    failed["attributes"]["ApproximateReceiveCount"] = "4"

    lambda_function._defer_retry(failed)

    change_visibility.assert_called_once_with(
        QueueUrl="https://sqs.example/queue",
        ReceiptHandle="receipt-message-1",
        VisibilityTimeout=40,
    )


def test_wake_uses_configured_runtime_timeout(monkeypatch):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b'{"status":"drained"}'
    urlopen = MagicMock(return_value=response)
    monkeypatch.setattr(lambda_function, "_access_token", lambda: "token")
    monkeypatch.setattr(lambda_function.urllib.request, "urlopen", urlopen)
    monkeypatch.setenv("AGENTCORE_RUNTIME_URL", "https://runtime.example")
    monkeypatch.setenv("RUNTIME_REQUEST_TIMEOUT_SECONDS", "420")

    lambda_function._wake("user-1", "session-1")

    assert urlopen.call_args.kwargs["timeout"] == 420
