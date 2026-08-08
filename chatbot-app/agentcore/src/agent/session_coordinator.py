"""Single-writer coordinator for durable session mailbox events."""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional, Sequence, Union
from uuid import uuid4

from agent.mailbox import (
    MailboxEvent,
    MailboxLease,
    MailboxRepository,
    SessionEvent,
)

logger = logging.getLogger(__name__)

PostAcknowledgeHook = Callable[[], Union[Awaitable[None], None]]


@dataclass(frozen=True)
class MailboxHandlerResult:
    """Durable projections plus work that is safe only after acknowledgement."""

    session_events: Sequence[SessionEvent] = ()
    after_ack: Optional[PostAcknowledgeHook] = None


MailboxEventHandler = Callable[
    [MailboxEvent],
    Awaitable[
        Optional[
            Union[
                Sequence[SessionEvent],
                MailboxHandlerResult,
            ]
        ]
    ],
]


class NonRetryableMailboxError(RuntimeError):
    """The event is valid but replay cannot make it succeed."""


@dataclass
class DrainResult:
    acquired: bool = False
    processed: int = 0
    retried: int = 0
    dead: int = 0
    post_ack_failed: int = 0
    lease_lost: bool = False


class SessionCoordinator:
    def __init__(
        self,
        repository: MailboxRepository,
        handlers: Dict[str, MailboxEventHandler],
        *,
        lease_seconds: int = 120,
        event_lease_seconds: int = 120,
        max_attempts: int = 5,
        retry_delay_seconds: int = 5,
        post_ack_attempts: int = 3,
    ):
        if lease_seconds < 3:
            raise ValueError("lease_seconds must be at least 3")
        if post_ack_attempts < 1:
            raise ValueError("post_ack_attempts must be at least 1")
        self.repository = repository
        self.handlers = dict(handlers)
        self.lease_seconds = lease_seconds
        self.event_lease_seconds = event_lease_seconds
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self.post_ack_attempts = post_ack_attempts

    async def drain(
        self,
        user_id: str,
        session_id: str,
        *,
        owner: Optional[str] = None,
        max_events: int = 20,
    ) -> DrainResult:
        result = DrainResult()
        owner = owner or f"coordinator-{uuid4().hex}"
        lease = await asyncio.to_thread(
            self.repository.acquire_lease,
            user_id,
            session_id,
            owner,
            lease_seconds=self.lease_seconds,
        )
        if lease is None:
            return result
        result.acquired = True

        active_lease = [lease]
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat(user_id, session_id, active_lease, lease_lost)
        )
        try:
            for _ in range(max_events):
                if lease_lost.is_set():
                    result.lease_lost = True
                    break

                event = await asyncio.to_thread(
                    self.repository.claim_next,
                    user_id,
                    session_id,
                    active_lease[0],
                    event_lease_seconds=self.event_lease_seconds,
                )
                if event is None:
                    break

                handler = self.handlers.get(event.event_type)
                try:
                    if handler is None:
                        raise NonRetryableMailboxError(
                            f"No handler registered for {event.event_type}"
                        )
                    handler_result = await handler(event)
                    if isinstance(handler_result, MailboxHandlerResult):
                        handled = handler_result
                    else:
                        handled = MailboxHandlerResult(
                            session_events=tuple(handler_result or ())
                        )
                except asyncio.CancelledError:
                    # An ambiguous side effect must not be made pending
                    # immediately. The event lease expiry is the recovery gate.
                    raise
                except Exception as error:
                    if lease_lost.is_set():
                        result.lease_lost = True
                        break
                    max_attempts = (
                        1 if isinstance(error, NonRetryableMailboxError)
                        else self.max_attempts
                    )
                    status = await asyncio.to_thread(
                        self.repository.retry,
                        event,
                        active_lease[0],
                        str(error),
                        delay_seconds=self.retry_delay_seconds,
                        max_attempts=max_attempts,
                    )
                    if status == "dead":
                        result.dead += 1
                    else:
                        result.retried += 1
                    logger.exception(
                        "[SessionCoordinator] Event %s failed with status %s",
                        event.event_id,
                        status,
                    )
                    if status != "dead":
                        # Do not consume all attempts in one drain when the
                        # retry delay is short or clock precision overlaps.
                        break
                    continue

                if lease_lost.is_set():
                    result.lease_lost = True
                    break
                acknowledged = await asyncio.to_thread(
                    self.repository.acknowledge,
                    event,
                    active_lease[0],
                    session_events=tuple(handled.session_events),
                )
                if not acknowledged:
                    result.lease_lost = True
                    break
                result.processed += 1
                if handled.after_ack is not None:
                    for attempt in range(1, self.post_ack_attempts + 1):
                        try:
                            if inspect.iscoroutinefunction(handled.after_ack):
                                await handled.after_ack()
                            else:
                                hook_result = await asyncio.to_thread(
                                    handled.after_ack
                                )
                                if inspect.isawaitable(hook_result):
                                    await hook_result
                            break
                        except Exception:
                            if attempt == self.post_ack_attempts:
                                result.post_ack_failed += 1
                                logger.exception(
                                    "[SessionCoordinator] Post-ack hook failed "
                                    "for event %s after %s attempts",
                                    event.event_id,
                                    attempt,
                                )
                            else:
                                await asyncio.sleep(0.1 * attempt)
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            if not lease_lost.is_set():
                await asyncio.to_thread(
                    self.repository.release_lease,
                    user_id,
                    session_id,
                    active_lease[0],
                )
        return result

    async def _heartbeat(
        self,
        user_id: str,
        session_id: str,
        active_lease: list[MailboxLease],
        lease_lost: asyncio.Event,
    ) -> None:
        interval = max(1.0, self.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            renewed = await asyncio.to_thread(
                self.repository.renew_lease,
                user_id,
                session_id,
                active_lease[0],
                lease_seconds=self.lease_seconds,
            )
            if renewed is None:
                lease_lost.set()
                return
            active_lease[0] = renewed
