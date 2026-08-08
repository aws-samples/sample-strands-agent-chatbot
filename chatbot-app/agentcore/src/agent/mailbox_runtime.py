"""Runtime bridge between durable mailbox storage and the FastAPI event loop."""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Dict, Optional, Sequence

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


def mailbox_write_enabled() -> bool:
    return os.environ.get("SESSION_MAILBOX_WRITE_ENABLED", "").lower() == "true"


def validate_mailbox_configuration() -> None:
    if mailbox_delivery_enabled() and not mailbox_write_enabled():
        raise RuntimeError(
            "SESSION_MAILBOX_DELIVERY_ENABLED requires "
            "SESSION_MAILBOX_WRITE_ENABLED"
        )


def register_mailbox_runtime(
    loop: asyncio.AbstractEventLoop,
    handlers: Dict[str, MailboxEventHandler],
) -> None:
    validate_mailbox_configuration()
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
    reconcile_event_ids: Sequence[str] = (),
) -> DrainResult:
    with _runtime_lock:
        handlers = dict(_handlers)
    coordinator = SessionCoordinator(get_mailbox_repository(), handlers)
    result = await coordinator.drain(
        user_id,
        session_id,
        owner=owner,
        max_events=max_events,
    )
    from agent.research_jobs import reconcile_processed_deliveries

    if reconcile_event_ids:
        await asyncio.to_thread(
            reconcile_processed_deliveries,
            user_id,
            session_id,
            list(reconcile_event_ids),
        )
    return result


async def notify_session_mailbox(
    user_id: str,
    session_id: str,
    *,
    event_ids: Sequence[str] = (),
) -> DrainResult:
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
        return await drain_session_mailbox(
            user_id,
            session_id,
            reconcile_event_ids=event_ids,
        )

    future = asyncio.run_coroutine_threadsafe(
        drain_session_mailbox(
            user_id,
            session_id,
            reconcile_event_ids=event_ids,
        ),
        loop,
    )
    return await asyncio.wrap_future(future)
