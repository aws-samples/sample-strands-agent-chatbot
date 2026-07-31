"""
Stop Signal Provider

DynamoDB-based out-of-band stop signal for graceful agent cancellation.
Used with agent.cancel() from Strands SDK to trigger cooperative shutdown.

Usage:
    from agent.stop_signal import get_stop_signal_provider

    provider = get_stop_signal_provider()
    if provider and provider.is_stop_requested(user_id, session_id, run_id):
        agent.cancel()  # SDK handles graceful shutdown
"""

import logging
import os
import threading
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class StopSignalProvider(ABC):
    """Abstract base class for run-scoped stop signal providers."""

    @abstractmethod
    def is_stop_requested(self, user_id: str, session_id: str, run_id: str) -> bool:
        """Check if stop has been requested for this run."""

    @abstractmethod
    def request_stop(self, user_id: str, session_id: str, run_id: str) -> None:
        """Request stop for this run."""

    @abstractmethod
    def clear_stop_signal(self, user_id: str, session_id: str, run_id: str) -> None:
        """Clear this run's stop signal after processing."""


class DynamoDBStopSignalProvider(StopSignalProvider):
    """
    Cloud deployment: DynamoDB-based out-of-band stop signal.
    Bypasses AgentCore Runtime's single-request-per-session limitation
    by writing/reading stop flags directly to DynamoDB.
    """

    def __init__(self, table_name: str):
        import boto3
        self._table_name = table_name
        region = os.environ.get("AWS_REGION", "us-west-2")
        self._client = boto3.client("dynamodb", region_name=region)

    def _get_key(self, user_id: str, session_id: str) -> dict:
        return {
            "userId": {"S": f"STOP#{user_id}"},
            "sk": {"S": f"SESSION#{session_id}"},
        }

    def is_stop_requested(self, user_id: str, session_id: str, run_id: str) -> bool:
        """Check for a stop signal matching the active run."""
        try:
            resp = self._client.get_item(
                TableName=self._table_name,
                Key=self._get_key(user_id, session_id),
                ProjectionExpression="runId",
            )
            item = resp.get("Item")
            if not item:
                return False
            stored_run_id = item.get("runId", {}).get("S")
            if stored_run_id == run_id:
                logger.info(f"[StopSignal] Stop detected for {user_id}:{session_id}:{run_id}")
                return True
            return False
        except Exception as e:
            logger.warning(f"[StopSignal] DynamoDB check failed: {e}")
            return False

    def request_stop(self, user_id: str, session_id: str, run_id: str) -> None:
        import time
        try:
            self._client.put_item(
                TableName=self._table_name,
                Item={
                    **self._get_key(user_id, session_id),
                    "runId": {"S": run_id},
                    "ttl": {"N": str(int(time.time()) + 300)},
                },
            )
            logger.info(f"[StopSignal] Stop set for {user_id}:{session_id}:{run_id}")
        except Exception as e:
            logger.warning(f"[StopSignal] DynamoDB put failed: {e}")
            raise

    def clear_stop_signal(self, user_id: str, session_id: str, run_id: str) -> None:
        try:
            self._client.delete_item(
                TableName=self._table_name,
                Key=self._get_key(user_id, session_id),
                ConditionExpression="runId = :run_id",
                ExpressionAttributeValues={":run_id": {"S": run_id}},
            )
            logger.info(f"[StopSignal] Stop cleared for {user_id}:{session_id}:{run_id}")
        except Exception as e:
            response = getattr(e, "response", {})
            code = response.get("Error", {}).get("Code")
            if code != "ConditionalCheckFailedException":
                logger.warning(f"[StopSignal] DynamoDB delete failed: {e}")


class InMemoryStopSignalProvider(StopSignalProvider):
    """Local-development provider shared by requests in one process."""

    def __init__(self):
        self._signals: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def is_stop_requested(self, user_id: str, session_id: str, run_id: str) -> bool:
        with self._lock:
            return self._signals.get((user_id, session_id)) == run_id

    def request_stop(self, user_id: str, session_id: str, run_id: str) -> None:
        with self._lock:
            self._signals[(user_id, session_id)] = run_id

    def clear_stop_signal(self, user_id: str, session_id: str, run_id: str) -> None:
        key = (user_id, session_id)
        with self._lock:
            if self._signals.get(key) == run_id:
                self._signals.pop(key, None)


_local_stop_events: dict[tuple[str, str, str], threading.Event] = {}
_local_stop_events_lock = threading.Lock()


def _local_key(user_id: str, session_id: str, run_id: str) -> tuple[str, str, str]:
    return user_id, session_id, run_id


def get_local_stop_event(user_id: str, session_id: str, run_id: str) -> threading.Event:
    """Return the in-process event used to interrupt an active tool worker."""
    key = _local_key(user_id, session_id, run_id)
    with _local_stop_events_lock:
        return _local_stop_events.setdefault(key, threading.Event())


def reset_local_stop_event(user_id: str, session_id: str, run_id: str) -> None:
    get_local_stop_event(user_id, session_id, run_id).clear()


def signal_local_stop(user_id: str, session_id: str, run_id: str) -> None:
    get_local_stop_event(user_id, session_id, run_id).set()


def clear_local_stop_event(user_id: str, session_id: str, run_id: str) -> None:
    key = _local_key(user_id, session_id, run_id)
    with _local_stop_events_lock:
        _local_stop_events.pop(key, None)


# Singleton instance cache
_provider_instance: StopSignalProvider = None
_provider_lock = threading.Lock()


def get_stop_signal_provider() -> StopSignalProvider | None:
    """Return the process-wide stop signal provider.

    Cloud deployments use DynamoDB for out-of-band delivery. Local development
    uses memory because the active execution and stop request share a process.
    """
    global _provider_instance

    if _provider_instance is None:
        with _provider_lock:
            if _provider_instance is None:
                table_name = os.environ.get("DYNAMODB_USERS_TABLE")
                if table_name:
                    logger.info(f"[StopSignal] Using DynamoDB provider (table={table_name})")
                    _provider_instance = DynamoDBStopSignalProvider(table_name)
                elif os.environ.get("ENVIRONMENT", "development") in {"development", "local"}:
                    logger.info("[StopSignal] Using in-memory provider for local development")
                    _provider_instance = InMemoryStopSignalProvider()
                else:
                    logger.error("[StopSignal] DYNAMODB_USERS_TABLE not set; stop signal disabled")
                    return None

    return _provider_instance
