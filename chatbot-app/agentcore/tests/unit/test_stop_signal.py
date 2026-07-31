"""Unit tests for run-scoped stop signal delivery."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src"))

from agent.stop_signal import (
    DynamoDBStopSignalProvider,
    InMemoryStopSignalProvider,
    clear_local_stop_event,
    get_local_stop_event,
    get_stop_signal_provider,
    reset_local_stop_event,
    signal_local_stop,
)


class TestDynamoDBStopSignalProvider:
    @pytest.fixture
    def provider_and_client(self):
        mock_client = MagicMock()
        with patch("boto3.client", return_value=mock_client):
            provider = DynamoDBStopSignalProvider("test-table")
        return provider, mock_client

    def test_matching_run_is_stopped(self, provider_and_client):
        provider, client = provider_and_client
        client.get_item.return_value = {"Item": {"runId": {"S": "run-1"}}}

        assert provider.is_stop_requested("user1", "sess1", "run-1") is True
        client.get_item.assert_called_once_with(
            TableName="test-table",
            Key={"userId": {"S": "STOP#user1"}, "sk": {"S": "SESSION#sess1"}},
            ProjectionExpression="runId",
        )

    def test_stale_run_is_ignored(self, provider_and_client):
        provider, client = provider_and_client
        client.get_item.return_value = {"Item": {"runId": {"S": "run-new"}}}

        assert provider.is_stop_requested("user1", "sess1", "run-old") is False

    def test_missing_or_failed_read_is_not_stopped(self, provider_and_client):
        provider, client = provider_and_client
        client.get_item.return_value = {}
        assert provider.is_stop_requested("user1", "sess1", "run-1") is False

        client.get_item.side_effect = Exception("DynamoDB timeout")
        assert provider.is_stop_requested("user1", "sess1", "run-1") is False

    def test_request_stop_writes_run_and_ttl(self, provider_and_client):
        provider, client = provider_and_client
        provider.request_stop("user1", "sess1", "run-1")

        item = client.put_item.call_args.kwargs["Item"]
        assert item["userId"]["S"] == "STOP#user1"
        assert item["sk"]["S"] == "SESSION#sess1"
        assert item["runId"]["S"] == "run-1"
        assert "ttl" in item
        assert "phase" not in item

    def test_clear_is_conditional_on_run_id(self, provider_and_client):
        provider, client = provider_and_client
        provider.clear_stop_signal("user1", "sess1", "run-1")

        client.delete_item.assert_called_once_with(
            TableName="test-table",
            Key={"userId": {"S": "STOP#user1"}, "sk": {"S": "SESSION#sess1"}},
            ConditionExpression="runId = :run_id",
            ExpressionAttributeValues={":run_id": {"S": "run-1"}},
        )


class TestInMemoryStopSignalProvider:
    def test_signal_is_run_scoped_and_conditionally_cleared(self):
        provider = InMemoryStopSignalProvider()
        provider.request_stop("user1", "sess1", "run-new")

        assert provider.is_stop_requested("user1", "sess1", "run-old") is False
        assert provider.is_stop_requested("user1", "sess1", "run-new") is True

        provider.clear_stop_signal("user1", "sess1", "run-old")
        assert provider.is_stop_requested("user1", "sess1", "run-new") is True

        provider.clear_stop_signal("user1", "sess1", "run-new")
        assert provider.is_stop_requested("user1", "sess1", "run-new") is False


class TestLocalStopEvents:
    def teardown_method(self):
        clear_local_stop_event("user1", "sess1", "run-1")

    def test_event_lifecycle(self):
        event = get_local_stop_event("user1", "sess1", "run-1")
        assert event.is_set() is False

        signal_local_stop("user1", "sess1", "run-1")
        assert event.is_set() is True

        reset_local_stop_event("user1", "sess1", "run-1")
        assert event.is_set() is False

        clear_local_stop_event("user1", "sess1", "run-1")
        assert get_local_stop_event("user1", "sess1", "run-1") is not event


class TestGetStopSignalProvider:
    def setup_method(self):
        import agent.stop_signal as module
        module._provider_instance = None

    def teardown_method(self):
        import agent.stop_signal as module
        module._provider_instance = None

    def test_uses_in_memory_provider_locally(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "development"}, clear=False):
            os.environ.pop("DYNAMODB_USERS_TABLE", None)
            provider = get_stop_signal_provider()
        assert isinstance(provider, InMemoryStopSignalProvider)

    def test_returns_none_for_misconfigured_cloud(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False):
            os.environ.pop("DYNAMODB_USERS_TABLE", None)
            provider = get_stop_signal_provider()
        assert provider is None

    def test_uses_dynamodb_and_caches_instance(self):
        with patch.dict(
            os.environ,
            {"DYNAMODB_USERS_TABLE": "my-table", "AWS_REGION": "us-east-1"},
        ), patch("boto3.client"):
            provider = get_stop_signal_provider()
            assert provider is get_stop_signal_provider()
        assert isinstance(provider, DynamoDBStopSignalProvider)
