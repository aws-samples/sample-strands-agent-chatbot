from unittest.mock import MagicMock

import lambda_function


def record(
    event_id: str,
    *,
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
            }
        },
    }


def test_coalesces_multiple_inserts_for_one_session(monkeypatch):
    wake = MagicMock()
    monkeypatch.setattr(lambda_function, "_wake", wake)

    result = lambda_function.lambda_handler(
        {"Records": [record("1"), record("2")]},
        None,
    )

    assert result == {"batchItemFailures": []}
    wake.assert_called_once_with("user-1", "session-1")


def test_ignores_non_inbox_stream_records(monkeypatch):
    wake = MagicMock()
    monkeypatch.setattr(lambda_function, "_wake", wake)

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
    wake.assert_not_called()


def test_reports_each_coalesced_record_when_wake_fails(monkeypatch):
    monkeypatch.setattr(
        lambda_function,
        "_wake",
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
