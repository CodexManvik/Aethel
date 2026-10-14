"""Knowing when it's Aethel's turn. A pause isn't a finished thought: System 1 judges whether the words so far are
complete. A finished one is answered after a short silence; an unfinished one gets a longer silence, in which more
speech joins the same turn. Without System 1 every turn waits the long silence (voice-activity detection alone:
the E3 baseline)."""
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

log = logging.getLogger("aethel.voice")
# The wording that did best on dev in scripts/eval_s1_eot.py (a choice beat a yes/no). Even so, Laya is close to
# chance at this: see VoiceSettings.system1_end_of_turn.
EOT_INSTRUCTIONS = "Look at the end of what the user said. Does it end where a complete sentence ends?"
EOT_OPTIONS = {
    "finished": "yes, it ends like a complete sentence",
    "continuing": "no, it ends part-way, for example on a word like and, but, the, to, I, because, or on a pause "
                  "sound like um",
}
EOT_TIMEOUT_S = 1.5   # System 1 runs locally on the CPU; a slow answer is no answer, the long silence applies
REPLY_CHARS = 400


def eot_state(utterance: str, previous_reply: str | None) -> dict:
    state = {"user_said": utterance}
    if previous_reply:
        state["aethel_last_reply"] = previous_reply[:REPLY_CHARS]
    return state


class EndOfTurn:
    def __init__(self, system1):
        self.system1 = system1  # None: not asked, every turn waits the long silence

    async def finished(self, utterance: str, previous_reply: str | None) -> float | None:
        """P(the thought is finished), or None when System 1 can't say."""
        if self.system1 is None:
            return None
        try:
            async with asyncio.timeout(EOT_TIMEOUT_S):
                answer = await self.system1.choice(eot_state(utterance, previous_reply), EOT_INSTRUCTIONS,
                                                   EOT_OPTIONS, "end_of_turn")
            return None if answer is None else answer[1]["finished"]
        except TimeoutError:
            log.info("end-of-turn judgment took over %.1fs; waiting the long silence", EOT_TIMEOUT_S)
            return None
        except Exception:  # a judge that fails must never leave the user's turn undecided
            log.exception("end-of-turn judgment failed; waiting the long silence")
            return None


@dataclass
class TurnDecision:
    text: str        # everything the user said in this turn
    waited_ms: int   # silence since they stopped speaking
    eot_p: float | None


class TurnManager:
    """One call's turn state. The clock and the sleep are injected, for tests."""

    def __init__(self, eot: EndOfTurn, *, threshold: float, short_wait_ms: int = 300, max_wait_ms: int = 1500,
                 sleep: Callable[[float], Awaitable] = asyncio.sleep, clock: Callable[[], float] = time.monotonic):
        self.eot, self.threshold = eot, threshold
        self.short_wait_ms, self.max_wait_ms = short_wait_ms, max_wait_ms
        self.sleep, self.clock = sleep, clock
        self._text = ""
        self._p: float | None = None
        self._since: float | None = None    # when the last speech ended
        self._speaking = False
        self._transcribing = 0              # utterances that ended but whose words haven't come back yet
        self._version = 0
        self._changed = asyncio.Event()

    def _bump(self) -> None:
        self._version += 1
        self._changed.set()

    async def on_utterance(self, text: str, previous_reply: str | None, silence_since: float | None = None) -> None:
        """The words of one utterance came back from speech recognition. `silence_since` is when that speech ended
        (the clock's time), so the time spent transcribing counts as silence already waited. Empty text (nothing
        recognised) adds no words. This never says the user stopped speaking: only `on_speech_end` does, because
        a later utterance may already be under way while an earlier one is still being transcribed."""
        text = text.strip()
        try:
            if text:
                self._text = f"{self._text} {text}".strip()
                self._p = await self.eot.finished(self._text, previous_reply)
        finally:
            self._transcribing = max(0, self._transcribing - 1)
            since = silence_since if silence_since is not None else self.clock()
            self._since = since if self._since is None else max(self._since, since)
            self._bump()

    def on_speech_start(self) -> None:
        """The user started speaking again: any pending decision is off, more is coming."""
        self._speaking = True
        self._bump()

    def on_speech_end(self, has_audio: bool = True) -> None:
        """The user stopped speaking. With audio, its words are on their way: no decision until they arrive (the
        silence timer would otherwise run out while the recognition of this very speech is still going)."""
        self._speaking = False
        if has_audio:
            self._transcribing += 1
        self._bump()

    def clear(self) -> None:
        self._text, self._p, self._since, self._speaking, self._transcribing = "", None, None, False, 0
        self._bump()

    async def _wait_change(self, version: int) -> None:
        while self._version == version:
            self._changed.clear()
            if self._version != version:
                break
            await self._changed.wait()

    async def next_turn(self) -> TurnDecision:
        """Wait until it's Aethel's turn, and say what the user said."""
        while True:
            version = self._version
            if not self._text or self._speaking or self._transcribing or self._since is None:
                await self._wait_change(version)
                continue
            finished = self._p is not None and self._p >= self.threshold
            wait_s = (self.short_wait_ms if finished else self.max_wait_ms) / 1000
            remaining = wait_s - (self.clock() - self._since)
            if remaining > 0 and await self._interrupted(remaining, version):
                continue
            decision = TurnDecision(self._text, max(0, round((self.clock() - self._since) * 1000)), self._p)
            self._text, self._p, self._since = "", None, None
            return decision

    async def _interrupted(self, seconds: float, version: int) -> bool:
        """Sleep; True if the user spoke (or something else changed) before the silence was over."""
        sleeper = asyncio.ensure_future(self.sleep(seconds))
        waiter = asyncio.ensure_future(self._wait_change(version))
        try:
            done, _ = await asyncio.wait({sleeper, waiter}, return_when=asyncio.FIRST_COMPLETED)
            return waiter in done
        finally:
            for task in (sleeper, waiter):
                task.cancel()
            await asyncio.gather(sleeper, waiter, return_exceptions=True)
