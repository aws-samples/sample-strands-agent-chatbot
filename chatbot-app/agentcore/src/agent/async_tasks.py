"""Tracks in-flight background work so /ping can report it to AgentCore Runtime.

AgentCore decides whether a runtime session is idle from the /ping status, not
from open connections: a session reporting "Healthy" is idle-eligible and its
microVM is terminated after idleRuntimeSessionTimeout (default 15 minutes),
while "HealthyBusy" keeps it alive up to maxLifetime (default 8 hours). Work
that outlives the request that started it therefore has to be registered here,
or the platform reclaims the container from under it.

Deliberately not bedrock_agentcore.runtime.BedrockAgentCoreApp: that class is a
Starlette subclass meant to *be* the application, and adopting it would mean
replacing the FastAPI app all the routers are mounted on. The behaviour we need
from it is the one line that maps "any active task" to HealthyBusy.

Threading, not asyncio: tools run in a ThreadPoolExecutor (see
skill_tools._run_async), so registrations can arrive off the event loop.
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
    """Register background work and return the id used to end it."""
    with _lock:
        task_id = next(_ids)
        _active[task_id] = {
            "name": name,
            "started_at": time.time(),
            "metadata": metadata or {},
        }
        count = len(_active)
    logger.info(f"[AsyncTask] Started {name} (id={task_id}, active={count})")
    return task_id


def end(task_id: int) -> bool:
    """Mark work complete. Returns False if the id was unknown or already ended."""
    with _lock:
        task = _active.pop(task_id, None)
        count = len(_active)
    if task is None:
        # Not an error worth raising: end() runs from finally blocks that may be
        # reached twice, and losing the container matters more than a stray id.
        logger.warning(f"[AsyncTask] Unknown task id {task_id}")
        return False
    elapsed = time.time() - task["started_at"]
    logger.info(
        f"[AsyncTask] Completed {task['name']} (id={task_id}, "
        f"{elapsed:.1f}s, active={count})"
    )
    return True


def ping_status() -> str:
    """The status string AgentCore Runtime expects from /ping.

    time_of_last_update is intentionally omitted: the platform tracks status
    changes itself, and a timestamp that advances on every ping reads as a
    continuous status change, which stops the idle timeout from ever firing and
    keeps sessions alive until maxLifetime.
    """
    with _lock:
        return "HealthyBusy" if _active else "Healthy"


def active_tasks() -> Dict[int, Dict[str, Any]]:
    """Snapshot of in-flight work. Used by tests to assert bookkeeping."""
    with _lock:
        return {task_id: dict(task) for task_id, task in _active.items()}


def reset() -> None:
    """Drop all registrations. For tests."""
    with _lock:
        _active.clear()
