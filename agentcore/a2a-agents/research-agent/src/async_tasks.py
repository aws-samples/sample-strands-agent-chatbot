"""Tracks in-flight research so /ping can report it to AgentCore Runtime.

AgentCore decides whether a runtime session is idle from the /ping status: a
session reporting "Healthy" is idle-eligible and its microVM is terminated after
idleRuntimeSessionTimeout (default 15 minutes), while "HealthyBusy" keeps it
alive up to maxLifetime (default 8 hours).

This matters here because the A2A SDK keeps consuming a task's events after the
client disconnects (DefaultRequestHandler.on_message_send_stream spawns a
background consumer on CancelledError). Research therefore outlives the request
that started it, and without this the platform can reclaim the container while
the report is still being written.

Kept separate from the orchestrator's copy: the two runtimes are independent
images with no shared package.
"""

import itertools
import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_active: Dict[int, Dict[str, Any]] = {}
_ids = itertools.count(1)


def begin(name: str, metadata: Optional[Dict[str, Any]] = None) -> int:
    """Register in-flight work and return the id used to end it."""
    with _lock:
        task_id = next(_ids)
        _active[task_id] = {"name": name, "started_at": time.time(), "metadata": metadata or {}}
        count = len(_active)
    logger.info(f"[AsyncTask] Started {name} (id={task_id}, active={count})")
    return task_id


def end(task_id: int) -> bool:
    """Mark work complete. Returns False if the id was unknown or already ended."""
    with _lock:
        task = _active.pop(task_id, None)
        count = len(_active)
    if task is None:
        # end() runs from finally blocks that can be reached twice; losing the
        # container would be worse than tolerating a stray id.
        logger.warning(f"[AsyncTask] Unknown task id {task_id}")
        return False
    logger.info(
        f"[AsyncTask] Completed {task['name']} (id={task_id}, "
        f"{time.time() - task['started_at']:.1f}s, active={count})"
    )
    return True


def ping_status() -> str:
    """The status string AgentCore Runtime expects from /ping.

    time_of_last_update is intentionally omitted: a timestamp that advances on
    every ping reads as a continuous status change, which stops the idle timeout
    from ever firing and keeps sessions alive until maxLifetime.
    """
    with _lock:
        return "HealthyBusy" if _active else "Healthy"


def reset() -> None:
    """Drop all registrations. For tests."""
    with _lock:
        _active.clear()
