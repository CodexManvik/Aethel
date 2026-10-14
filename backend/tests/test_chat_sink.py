import asyncio

import pytest

from aethel.protocol import UserMessage
from aethel.services import build_services
from tests.fakes import FakeLocal, FakeProvider, factory_from

pytestmark = pytest.mark.anyio


def make(provider):
    svc = build_services(provider_factory=factory_from({"groq:g": provider}), local_llm=FakeLocal(fail="none"))
    svc.keys.set_many({"groq": "k"})
    svc.settings.update({"roles": {"chat": [{"provider": "groq", "model": "g"}]}})
    return svc, svc.conversations.create()


def event(conv, text="hi"):
    return UserMessage(conversation_id=conv.id, text=text)


async def test_the_sink_gets_exactly_the_streamed_deltas_in_order():
    svc, conv = make(FakeProvider(chunks=["One", " two", " three."]))
    seen = []

    async def sink(text):
        seen.append(text)

    result = await svc.chat.run_turn(event(conv), sink=sink)
    assert seen == ["One", " two", " three."] and result.status == "complete"
    assert svc.messages.get(result.assistant_message_id).content == "One two three."
    assert svc.messages.get(result.user_message_id).content == "hi"


async def test_meta_is_stored_on_both_messages_and_survives_the_recalled_context():
    svc, conv = make(FakeProvider(chunks=["Ok."]))
    result = await svc.chat.run_turn(event(conv), meta={"modality": "voice"})
    assert svc.messages.get(result.user_message_id).meta["modality"] == "voice"
    assert svc.messages.get(result.assistant_message_id).meta["modality"] == "voice"


async def test_a_typed_turn_has_no_extra_meta():
    svc, conv = make(FakeProvider(chunks=["Ok."]))
    result = await svc.chat.run_turn(event(conv))
    assert svc.messages.get(result.user_message_id).meta == {}


async def test_a_sink_that_raises_does_not_kill_the_turn():
    svc, conv = make(FakeProvider(chunks=["a", "b"]))
    calls = []

    async def sink(text):
        calls.append(text)
        raise RuntimeError("tts exploded")

    result = await svc.chat.run_turn(event(conv), sink=sink)
    assert result.status == "complete" and calls == ["a", "b"]
    assert svc.messages.get(result.assistant_message_id).content == "ab"


async def test_started_names_the_messages_so_the_reply_can_be_stopped_while_it_streams():
    svc, conv = make(FakeProvider(chunks=["a", "b", "c", "d"], delay=0.05))
    ids = {}

    def started(user_id, assistant_id):
        ids.update(user=user_id, assistant=assistant_id)

    first = asyncio.Event()

    async def sink(text):
        first.set()

    task = asyncio.ensure_future(svc.chat.run_turn(event(conv), sink=sink, started=started))
    await asyncio.wait_for(first.wait(), 2)
    assert await svc.chat.stop(ids["assistant"])
    result = await asyncio.wait_for(task, 2)
    assert result.status == "stopped" and result.assistant_message_id == ids["assistant"]
    assert svc.messages.get(ids["assistant"]).status == "stopped"
    assert len(svc.messages.get(ids["assistant"]).content) < 4


async def test_an_unknown_conversation_is_an_error_result():
    svc, _ = make(FakeProvider())
    result = await svc.chat.run_turn(UserMessage(conversation_id="nope", text="hi"))
    assert result.status == "error" and result.user_message_id is None
