"""Fan-out of server events to every connected socket.

There's a single user and possibly several windows, and a turn or task can
outlive the socket that started it. So every event is broadcast and the
frontend filters by conversation, message or task id."""
from typing import Awaitable, Callable

from pydantic import BaseModel

Send = Callable[[str], Awaitable[None]]


class EventHub:
    def __init__(self) -> None:
        self._subs: dict[int, Send] = {}
        self._next = 0

    def subscribe(self, send: Send) -> Callable[[], None]:
        key = self._next
        self._next += 1
        self._subs[key] = send
        return lambda: self._subs.pop(key, None)

    async def publish(self, event: BaseModel) -> None:
        payload = event.model_dump_json()
        for key, send in list(self._subs.items()):
            try:
                await send(payload)
            except Exception:
                self._subs.pop(key, None)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)
