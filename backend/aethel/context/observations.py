"""Keeping tool results in the model's context only while they're worth their tokens
(token-efficiency spec §4.2, §5.1).

State observations ("screen", "page") describe how something looks now; a newer one of the same kind
supersedes the older. Content observations (a file, a web page, search results) are never stale."""
from dataclasses import dataclass


class StateTracker:
    """The latest content of each kind of state, so an identical one isn't sent twice."""

    def __init__(self) -> None:
        self._last: dict[str, tuple[str, int]] = {}

    def seen(self, kind: str, content: str, step_no: int) -> str | None:
        """A short note to send instead when `content` is the same as the last one of this kind, else None."""
        last = self._last.get(kind)
        if last is not None and last[0] == content:
            return f"[unchanged since step {last[1]}: same {kind} as then]"
        self._last[kind] = (content, step_no)
        return None
