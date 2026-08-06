"""
Execution Registry - tracks active/recent agent executions with event buffers.

Decouples agent execution lifecycle from SSE connection lifecycle.
Agent runs as a background task, appending events to a buffer.
SSE connections tail the buffer, enabling reconnection with cursor-based replay.
"""

import asyncio
import time
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


class ExecutionStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass
class SSEEvent:
    event_id: int
    data: str           # Original SSE string: "data: {...}\n\n"
    timestamp: float
    event_type: str     # "init", "response", "tool_use", "complete", etc.


@dataclass
class Execution:
    execution_id: str        # "{session_id}:{run_id}"
    session_id: str
    user_id: str
    status: ExecutionStatus
    events: list[SSEEvent] = field(default_factory=list)
    next_event_id: int = 1
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    task: Optional[asyncio.Task] = None
    subscribers: int = 0
    _new_event: asyncio.Event = field(default_factory=asyncio.Event)

    # Media type for the response stream (supports AG-UI content types)
    media_type: str = "text/event-stream"

    MAX_EVENTS = 10000
    TRUNCATE_RATIO = 0.2  # Remove oldest 20% when overflow

    def append_event(self, data: str, event_type: str = "unknown") -> SSEEvent:
        """Append an event to the buffer and notify subscribers."""
        # Overflow protection
        if len(self.events) >= self.MAX_EVENTS:
            trim_count = int(self.MAX_EVENTS * self.TRUNCATE_RATIO)
            self.events = self.events[trim_count:]
            # Insert truncation marker
            marker = SSEEvent(
                event_id=self.next_event_id,
                data='data: {"type":"custom","name":"buffer_truncated"}\n\n',
                timestamp=time.time(),
                event_type="buffer_truncated",
            )
            self.next_event_id += 1
            self.events.insert(0, marker)

        event = SSEEvent(
            event_id=self.next_event_id,
            data=data,
            timestamp=time.time(),
            event_type=event_type,
        )
        self.next_event_id += 1
        self.events.append(event)

        # Wake up all subscribers waiting for new events.
        # Do NOT clear here — subscribers clear after waking to avoid race.
        self._new_event.set()

        return event

    def get_events_from(self, cursor: int) -> list[SSEEvent]:
        """Return events with event_id > cursor."""
        return [e for e in self.events if e.event_id > cursor]


class ExecutionRegistry:
    """Singleton registry for active/recent agent executions."""

    BUFFER_TTL_AFTER_COMPLETE = 300  # 5 minutes after completion

    _instance: Optional["ExecutionRegistry"] = None

    def __new__(cls) -> "ExecutionRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._executions = {}
            cls._instance._session_latest = {}
            cls._instance._lock = asyncio.Lock()
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        cls._instance = None

    async def create_execution(
        self,
        session_id: str,
        user_id: str,
        run_id: str,
        *,
        supersede_running: bool = True,
    ) -> Optional[Execution]:
        async with self._lock:
            latest_id = self._session_latest.get(session_id)
            if latest_id:
                latest = self._executions.get(latest_id)
                if latest and latest.status == ExecutionStatus.RUNNING:
                    if not supersede_running:
                        return None
                    # Force-complete the stale execution (stop was already requested)
                    logger.warning(
                        f"[ExecutionRegistry] Superseding stale execution {latest_id} "
                        f"for session {session_id}"
                    )
                    latest.status = ExecutionStatus.STOPPED
                    latest.completed_at = time.time()
                    if latest.task and not latest.task.done():
                        latest.task.cancel()

            execution_id = f"{session_id}:{run_id}"
            execution = Execution(
                execution_id=execution_id,
                session_id=session_id,
                user_id=user_id,
                status=ExecutionStatus.RUNNING,
            )
            self._executions[execution_id] = execution
            self._session_latest[session_id] = execution_id
            logger.info(f"[ExecutionRegistry] Created execution {execution_id}")
            return execution

    def get_execution(self, execution_id: str) -> Optional[Execution]:
        return self._executions.get(execution_id)

    def get_latest_execution(self, session_id: str) -> Optional[Execution]:
        execution_id = self._session_latest.get(session_id)
        if execution_id:
            return self._executions.get(execution_id)
        return None

    async def cleanup_expired(self) -> int:
        """Remove completed executions past their TTL. Returns count removed."""
        async with self._lock:
            now = time.time()
            to_remove = []
            for eid, execution in self._executions.items():
                if (
                    execution.status != ExecutionStatus.RUNNING
                    and execution.completed_at
                    and (now - execution.completed_at) > self.BUFFER_TTL_AFTER_COMPLETE
                    and execution.subscribers == 0
                ):
                    to_remove.append(eid)

            for eid in to_remove:
                execution = self._executions.pop(eid, None)
                if execution:
                    # Clean up session_latest if it points to this execution
                    if self._session_latest.get(execution.session_id) == eid:
                        del self._session_latest[execution.session_id]

            if to_remove:
                logger.info(f"[ExecutionRegistry] Cleaned up {len(to_remove)} expired executions")
            return len(to_remove)
