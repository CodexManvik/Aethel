import asyncio

import pytest

from aethel.voice.turns import EOT_Q, EndOfTurn, TurnManager, eot_state

pytestmark = pytest.mark.anyio


class ManualClock:
    """Time that only moves when the test says so; sleeps end when it passes them."""

    def __init__(self):
        self.now = 0.0
        self._sleepers: list[tuple[float, asyncio.Future]] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        fut = asyncio.get_running_loop().create_future()
        self._sleepers.append((self.now + seconds, fut))
        await fut

    async def advance(self, seconds: float) -> None:
        self.now += seconds
        for deadline, fut in list(self._sleepers):
            if deadline <= self.now + 1e-9 and not fut.done():
                fut.set_result(None)
        for _ in range(5):
            await asyncio.sleep(0)


class FakeEOT:
    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    async def finished(self, utterance, previous_reply):
        self.asked.append((utterance, previous_reply))
        return self.answers.pop(0) if self.answers else None


def manager(eot, clock, **kw):
    return TurnManager(eot, threshold=0.6, sleep=clock.sleep, clock=clock, **kw)


async def decision_after(clock, turns, seconds):
    task = asyncio.ensure_future(turns.next_turn())
    await asyncio.sleep(0)
    await clock.advance(seconds)
    return task


async def test_a_finished_thought_is_answered_after_the_short_silence():
    clock = ManualClock()
    turns = manager(FakeEOT(0.9), clock)
    await turns.on_utterance("What time does it open?", None)
    task = asyncio.ensure_future(turns.next_turn())
    await clock.advance(0.299)
    assert not task.done()
    await clock.advance(0.002)
    decision = await asyncio.wait_for(task, 1)
    assert (decision.text, decision.waited_ms, decision.eot_p) == ("What time does it open?", 301, 0.9)


async def test_an_unfinished_thought_waits_the_long_silence():
    clock = ManualClock()
    turns = manager(FakeEOT(0.2), clock)
    await turns.on_utterance("so I was thinking that", None)
    task = asyncio.ensure_future(turns.next_turn())
    await clock.advance(1.499)
    assert not task.done()
    await clock.advance(0.002)
    decision = await asyncio.wait_for(task, 1)
    assert decision.text == "so I was thinking that" and decision.eot_p == 0.2 and decision.waited_ms == 1501


async def test_speaking_again_joins_the_turn_and_the_judgment_is_asked_again_on_the_joined_text():
    clock = ManualClock()
    eot = FakeEOT(0.2, 0.9)
    turns = manager(eot, clock)
    await turns.on_utterance("I need a library", "Sure.")
    task = asyncio.ensure_future(turns.next_turn())
    await clock.advance(0.6)
    turns.on_speech_start()
    await clock.advance(0.5)           # still speaking: nothing may be decided, however long
    assert not task.done()
    await turns.on_utterance("that opens on Sundays", "Sure.", silence_since=clock.now)
    await clock.advance(0.3)
    decision = await asyncio.wait_for(task, 1)
    assert decision.text == "I need a library that opens on Sundays" and decision.eot_p == 0.9
    assert eot.asked == [("I need a library", "Sure."), ("I need a library that opens on Sundays", "Sure.")]


async def test_without_system_one_every_turn_waits_the_long_silence():
    clock = ManualClock()
    turns = manager(FakeEOT(), clock)  # answers None
    await turns.on_utterance("hello there", None)
    task = asyncio.ensure_future(turns.next_turn())
    await clock.advance(1.4)
    assert not task.done()
    await clock.advance(0.2)
    decision = await asyncio.wait_for(task, 1)
    assert decision.eot_p is None and decision.text == "hello there"


async def test_the_time_spent_transcribing_counts_as_silence_already_waited():
    clock = ManualClock()
    turns = manager(FakeEOT(0.9), clock)
    clock.now = 0.5                      # transcription finished 0.5 s after speech ended
    await turns.on_utterance("hello", None, silence_since=0.0)
    decision = await asyncio.wait_for(turns.next_turn(), 1)   # the 0.3 s is long gone: no sleeping at all
    assert decision.waited_ms == 500


async def test_nothing_recognised_adds_no_words_and_does_not_decide_by_itself():
    clock = ManualClock()
    turns = manager(FakeEOT(), clock)
    turns.on_speech_start()
    await turns.on_utterance("  ", None)
    task = asyncio.ensure_future(turns.next_turn())
    await clock.advance(5)
    assert not task.done()             # no words, no turn
    task.cancel()


async def test_a_cancelled_wait_leaves_no_stray_tasks():
    clock = ManualClock()
    turns = manager(FakeEOT(0.9), clock)
    await turns.on_utterance("hello", None)
    task = asyncio.ensure_future(turns.next_turn())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    names = [t.get_coro().__qualname__ for t in asyncio.all_tasks() if not t.done()]
    assert not [n for n in names if "next_turn" in n or "_wait_change" in n or "sleep" in n]


def test_eot_state_truncates_the_previous_reply():
    assert eot_state("yes", None) == {"user_said": "yes"}
    state = eot_state("yes", "x" * 1000)
    assert state["user_said"] == "yes" and len(state["aethel_last_reply"]) == 400


async def test_end_of_turn_asks_system_one_and_gives_up_on_a_slow_answer(monkeypatch):
    class S1:
        async def noul(self, state, instructions, purpose):
            self.seen = (state, instructions, purpose)
            return 0.8

    s1 = S1()
    assert await EndOfTurn(s1).finished("thanks", "Anytime.") == 0.8
    assert s1.seen == ({"user_said": "thanks", "aethel_last_reply": "Anytime."}, EOT_Q, "end_of_turn")

    class Slow:
        async def noul(self, *args):
            await asyncio.sleep(10)

    monkeypatch.setattr("aethel.voice.turns.EOT_TIMEOUT_S", 0.01)
    assert await EndOfTurn(Slow()).finished("thanks", None) is None

    class Off:
        async def noul(self, *args):
            return None

    assert await EndOfTurn(Off()).finished("thanks", None) is None
