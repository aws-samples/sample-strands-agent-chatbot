"""Runtime bridge between durable mailbox storage and the FastAPI event loop."""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Dict, Optional

from agent.mailbox import get_mailbox_repository
from agent.session_coordinator import (
    DrainResult,
    MailboxEventHandler,
    SessionCoordinator,
)


_runtime_loop: Optional[asyncio.AbstractEventLoop] = None
_handlers: Dict[str, MailboxEventHandler] = {}
_runtime_lock = threading.Lock()


def mailbox_delivery_enabled() -> bool:
    return os.environ.get("SESSION_MAILBOX_DELIVERY_ENABLED", "").lower() == "true"


def register_mailbox_runtime(
    loop: asyncio.AbstractEventLoop,
    handlers: Dict[str, MailboxEventHandler],
) -> None:
    global _runtime_loop, _handlers
    with _runtime_lock:
        _runtime_loop = loop
        _handlers = dict(handlers)


def clear_mailbox_runtime() -> None:
    global _runtime_loop, _handlers
    with _runtime_lock:
        _runtime_loop = None
        _handlers = {}


async def drain_session_mailbox(
    user_id: str,
    session_id: str,
    *,
    owner: Optional[str] = None,
    max_events: int = 20,
) -> DrainResult:
    with _runtime_lock:
        handlers = dict(_handlers)
    coordinator = SessionCoordinator(get_mailbox_repository(), handlers)
    return await coordinator.drain(
        user_id,
        session_id,
        owner=owner,
        max_events=max_events,
    )


async def notify_session_mailbox(user_id: str, session_id: str) -> DrainResult:
    """Request a drain from any worker thread/event loop."""
    with _runtime_lock:
        loop = _runtime_loop
    if loop is None or loop.is_closed():
        raise RuntimeError("Session mailbox runtime is not available")

    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if current_loop is loop:
        return await drain_session_mailbox(user_id, session_id)

    future = asyncio.run_coroutine_threadsafe(
        drain_session_mailbox(user_id, session_id),
        loop,
    )
    return await asyncio.wrap_future(future)
