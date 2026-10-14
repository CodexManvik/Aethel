"""Task narration in a call: while a task started by voice runs, Aethel says now and then how it is going, in one
short in-character sentence, and reads out the task's own summary when it ends. Narration is never saved (the
task's final message is) and never talks over the user or over a reply."""
import asyncio
import json
import logging
import time
from typing import Awaitable, Callable

from ..chat.persona import system_prompt
from ..providers.base import ChatMessage, TextDelta

log = logging.getLogger("aethel.voice")
TERMINAL = {"done", "failed", "cancelled"}
DEBOUNCE_S = 1.0   # gather the first few steps into one line rather than narrating the very first one alone
MAX_STEPS = 6
END_LINE_CHARS = 240     # a task's own summary can be long: read out its start, the full text is in the chat
END_WAIT_S = 120.0       # how long the end of a task waits for the user (or a reply) to finish before it's dropped
RETRY_S = 0.5


class Narrator:
    def __init__(self, *, router, hub, speak: Callable[[str], Awaitable[None]], busy: Callable[[], bool],
                 min_gap_s: float = 8.0, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable] = asyncio.sleep):
        self.router, self.speak, self.busy = router, speak, busy
        self.min_gap_s, self.clock, self.sleep = min_gap_s, clock, sleep
        self._goals: dict[str, str] = {}          # task id -> goal, for the tasks this call started
        self._steps: dict[str, list[str]] = {}    # step summaries since the last line
        self._last_spoken = float("-inf")
        self._pending: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._unsubscribe = hub.subscribe(self._on_event)

    def follow(self, task_id: str, goal: str) -> None:
        self._goals[task_id] = goal
        self._steps.setdefault(task_id, [])

    def unfollow(self, task_id: str) -> None:
        self._goals.pop(task_id, None)
        self._steps.pop(task_id, None)

    async def close(self) -> None:
        self._unsubscribe()
        tasks = [t for t in [self._pending, *self._tasks] if t is not None]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _on_event(self, payload: str) -> None:
        try:
            ev = json.loads(payload)
        except ValueError:
            return
        task_id = ev.get("task_id")
        if task_id not in self._goals:
            return
        if ev.get("type") == "step_started" and ev.get("summary"):
            self._steps[task_id] = (self._steps[task_id] + [ev["summary"]])[-MAX_STEPS:]
            if self._pending is None or self._pending.done():
                self._pending = asyncio.ensure_future(self._narrate_later(task_id))
        elif ev.get("type") == "task_state" and ev.get("state") in TERMINAL:
            self._goals.pop(task_id, None)
            self._steps.pop(task_id, None)
            line = ev.get("summary") or ev.get("message_text") or (
                f"The task stopped: {ev['error']}" if ev.get("error") else None)
            if line:  # the task's own words, no model call; it waits its turn rather than being dropped
                self._spawn(self._say_end(_shorten(line)))

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _say_end(self, line: str) -> None:
        waited = 0.0
        while self.busy() and waited < END_WAIT_S:
            await self.sleep(RETRY_S)
            waited += RETRY_S
        await self._say(line)

    async def _say(self, line: str) -> None:
        if self.busy():
            return
        self._last_spoken = self.clock()
        try:
            await self.speak(line)
        except Exception:
            log.exception("speaking a narration line failed")

    async def _narrate_later(self, task_id: str) -> None:
        await self.sleep(max(DEBOUNCE_S, self.min_gap_s - (self.clock() - self._last_spoken)))
        steps, goal = self._steps.get(task_id), self._goals.get(task_id)
        if not steps or goal is None:
            return
        self._steps[task_id] = []
        if self.busy():  # the user is talking, or a reply is playing: these steps are old news by the next line
            return
        line = await self._line(goal, steps)
        if line and task_id in self._goals:
            await self._say(line)

    async def _line(self, goal: str, steps: list[str]) -> str:
        prompt = [
            ChatMessage("system", system_prompt() + "\n\nYou are on a voice call while you work on a task for the "
                        "user. In ONE short spoken sentence (under 15 words), in your own voice, tell them how it is "
                        "going. No lists, no markdown, no questions."),
            ChatMessage("user", f"The task: {goal}\nWhat you have done since you last said anything:\n"
                        + "\n".join(f"- {s}" for s in steps)),
        ]
        parts: list[str] = []
        try:
            async for ev in self.router.stream("chat", prompt, max_tokens=40, temperature=0.7, purpose="narration"):
                if isinstance(ev, TextDelta):
                    parts.append(ev.text)
        except Exception as exc:
            log.info("no narration line: %r", exc)
            return ""
        return " ".join("".join(parts).split())


def _shorten(text: str) -> str:
    """At most END_LINE_CHARS, cut at a sentence end if there is one."""
    text = " ".join(text.split())
    if len(text) <= END_LINE_CHARS:
        return text
    head = text[:END_LINE_CHARS]
    end = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    return head[: end + 1] if end > 40 else head.rsplit(" ", 1)[0] + "…"
