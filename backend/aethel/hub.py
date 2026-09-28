"""Fan-out of server events to every connected socket.

There's a single user and possibly several windows, and a turn or task can
outlive the socket that started it. So every event is broadcast and the
frontend filters by conversation, message or task id.

A subscriber that fails or stalls is dropped, and told so (its socket is
closed), so the window reconnects and catches up instead of going deaf and
holding every other window's events up."""
import asyncio
import logging
from typing import Awaitable, Callable

from pydantic import BaseModel

Send = Callable[[str], Awaitable[None]]
SEND_TIMEOUT_S = 5.0
log = logging.getLogger("aethel.hub")


class EventHub:
    def __init__(self) -> None:
        self._subs: dict[int, tuple[Send, Callable[[], object] | None]] = {}
        self._next = 0

    def subscribe(self, send: Send, on_drop: Callable[[], object] | None = None) -> Callable[[], None]:
        key = self._next
        self._next += 1
        self._subs[key] = (send, on_drop)
        return lambda: self._subs.pop(key, None)

    async def publish(self, event: BaseModel) -> None:
        payload = event.model_dump_json()
        for key, (send, on_drop) in list(self._subs.items()):
            try:
                # Not wait_for: on 3.11 it swallows a cancel that lands as the send completes.
                async with asyncio.timeout(SEND_TIMEOUT_S):
                    await send(payload)
            except Exception as exc:
                self._subs.pop(key, None)
                log.warning("dropped an event subscriber: %r", exc)
                if on_drop is not None:
                    on_drop()

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)
