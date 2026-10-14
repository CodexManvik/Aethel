import asyncio

import pytest

from aethel.hub import EventHub
from aethel.protocol import StepStarted, TaskState
from aethel.providers.base import StreamDone, TextDelta
from aethel.voice.narrator import Narrator

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    async def sleep(self, s):
        self.now += s
        await asyncio.sleep(0)


class Router:
    def __init__(self):
        self.calls = []

    async def stream(self, role, messages, **kw):
        self.calls.append((role, messages, kw))
        yield TextDelta("Notepad is open, ")
        yield TextDelta("writing now.")
        yield StreamDone("stop")


def step(task_id, summary):
    return StepStarted(task_id=task_id, step_id=summary, tool="t", summary=summary, verdict="allow")


async def setup(busy=lambda: False):
    hub, router, clock, spoken = EventHub(), Router(), Clock(), []

    async def speak(line):
        spoken.append(line)

    n = Narrator(router=router, hub=hub, speak=speak, busy=busy, clock=clock, sleep=clock.sleep)
    n.follow("t1", "Write a haiku in Notepad")
    return n, hub, router, clock, spoken


async def settle():
    for _ in range(10):
        await asyncio.sleep(0)


async def test_five_steps_close_together_make_one_line_through_the_chat_model():
    n, hub, router, clock, spoken = await setup()
    for i in range(5):
        await hub.publish(step("t1", f"Step {i}"))
    await settle()
    assert spoken == ["Notepad is open, writing now."]
    role, messages, kw = router.calls[0]
    assert role == "chat" and kw["purpose"] == "narration" and kw["max_tokens"] == 40
    assert "Write a haiku in Notepad" in messages[1].content and "- Step 4" in messages[1].content
    await n.close()


async def test_the_next_line_waits_the_gap():
    n, hub, router, clock, spoken = await setup()
    await hub.publish(step("t1", "a"))
    await settle()
    clock.now += 3
    await hub.publish(step("t1", "b"))
    await settle()
    assert len(spoken) == 2 and clock.now >= 8   # the second one came only after the gap
    await n.close()


async def test_dropped_while_the_user_speaks_or_a_reply_plays():
    n, hub, router, clock, spoken = await setup(busy=lambda: True)
    await hub.publish(step("t1", "a"))
    await settle()
    await hub.publish(TaskState(task_id="t1", conversation_id="c", state="done", summary="Done."))
    await settle()
    assert spoken == [] and router.calls == []
    await n.close()


async def test_the_end_of_the_task_is_spoken_verbatim_without_a_model_call():
    n, hub, router, clock, spoken = await setup()
    await hub.publish(TaskState(task_id="t1", conversation_id="c", state="done", summary="I wrote the haiku and saved it."))
    await settle()
    assert spoken == ["I wrote the haiku and saved it."] and router.calls == []
    await hub.publish(step("t1", "late"))   # no longer followed
    await settle()
    assert len(spoken) == 1
    await n.close()


async def test_other_tasks_are_ignored_and_close_unsubscribes():
    n, hub, router, clock, spoken = await setup()
    await hub.publish(step("other", "a"))
    await settle()
    assert spoken == []
    await n.close()
    assert hub.subscriber_count == 0
