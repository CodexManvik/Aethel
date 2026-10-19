import pytest
from fastapi.testclient import TestClient

from aethel.app import create_app
from aethel.chat.persona import system_prompt
from aethel.personas.model import Persona, PersonaVoice
from aethel.personas.prompt import BACKSTORY_MAX, persona_system_prompt, speaking_hint
from aethel.personas.store import builtin_persona
from aethel.runtime.prompts import FINISH_TASK, finish_task_spec
from aethel.services import build_services
from tests.fakes import FakeLocal, FakeProvider, factory_from
from tests.test_voice_session import Call

MIRA = Persona(id="mira", name="Mira", voice=PersonaVoice(blend={"af_heart": 0.6, "bf_emma": 0.4}, speed=1.1),
               sections={"Personality": "Curious and dry-humoured.", "Backstory": "Grew up by the sea. " * 60,
                         "Speaking style": "short lowercase bursts", "Boundaries": "No medical advice.",
                         "Hobbies": "Film photography."})


def test_builtin_prompt_is_unchanged():
    assert persona_system_prompt(builtin_persona()) == system_prompt()


def test_companion_prompt_shape():
    text = persona_system_prompt(MIRA, "Uses 'lol' a lot.")
    assert text.startswith("You are Mira.\n\nCurious and dry-humoured.\n\nBackstory:\n")
    backstory = text.split("Backstory:\n", 1)[1].split("\n\n", 1)[0]
    assert len(backstory) == BACKSTORY_MAX and backstory.endswith("…")
    assert text.index("Speaking style:") < text.index("Boundaries:") < text.index("Hobbies:")
    assert text.endswith("How you write (learned from real messages):\nUses 'lol' a lot.")


def test_no_double_name_line():
    p = MIRA.model_copy(update={"sections": {"Personality": "You are Mira, a sailor."}})
    assert persona_system_prompt(p).startswith("You are Mira, a sailor.")


def test_finish_task_speaks_as_the_persona():
    assert finish_task_spec(speaking_hint(builtin_persona())) is FINISH_TASK
    spec = finish_task_spec(speaking_hint(MIRA))
    assert spec.description.endswith("Speak as Mira: short lowercase bursts") and spec.parameters == FINISH_TASK.parameters


def _client(provider):
    svc = build_services(provider_factory=factory_from({"groq:g": provider}), local_llm=FakeLocal(fail="none"))
    svc.keys.set_many({"groq": "k"})
    svc.settings.update({"roles": {"chat": [{"provider": "groq", "model": "g"}]}})
    return TestClient(create_app(svc)), svc


def _turn(ws, conv_id, text):
    ws.send_json({"type": "user_message", "conversation_id": conv_id, "text": text})
    while ws.receive_json()["type"] != "message_end":
        pass


def test_chat_speaks_as_the_conversations_persona():
    provider = FakeProvider(chunks=["hey"])
    client, svc = _client(provider)
    mira = svc.personas.create(MIRA)
    svc.personas.set_style_directive(mira.id, "Uses 'lol' a lot.")
    with client:
        conv = client.post("/api/conversations", json={"persona_id": mira.id}).json()
        assert conv["persona_id"] == "mira"
        with client.websocket_connect("/ws/session") as ws:
            _turn(ws, conv["id"], "hello")
    system = provider.calls[0][0].content
    persona, _, time_line = system.rpartition("\n\n")
    assert persona == persona_system_prompt(mira, "Uses 'lol' a lot.")
    assert time_line.startswith("Current local time: ")


def test_a_deleted_persona_falls_back_to_the_builtin():
    provider = FakeProvider(chunks=["hey"])
    client, svc = _client(provider)
    mira = svc.personas.create(MIRA)
    with client:
        conv = client.post("/api/conversations", json={"persona_id": mira.id}).json()
        svc.personas.delete(mira.id)
        with client.websocket_connect("/ws/session") as ws:
            _turn(ws, conv["id"], "hello")
    assert provider.calls[0][0].content.rpartition("\n\n")[0] == system_prompt()


def test_unknown_persona_is_422():
    client, _ = _client(FakeProvider())
    with client:
        assert client.post("/api/conversations", json={"persona_id": "nobody"}).status_code == 422
        assert client.post("/api/conversations", json={"persona_id": "../x"}).status_code == 422
        assert client.post("/api/conversations", json={}).json()["persona_id"] == "aethel"


def test_task_engine_reads_the_persona():
    svc = build_services(provider_factory=factory_from({}), local_llm=FakeLocal(fail="none"))
    mira = svc.personas.create(MIRA)
    conv = svc.conversations.create(persona_id=mira.id)
    svc.engine._conv["t1"] = conv.id
    assert svc.engine._speaking("t1") == "Speak as Mira: short lowercase bursts"
    svc.engine._conv["t2"] = svc.conversations.create().id
    assert svc.engine._speaking("t2") is None


@pytest.mark.anyio
async def test_call_uses_the_personas_voice_and_name():
    call = Call(hello=False)
    async with call:
        mira = call.svc.personas.create(MIRA)
        conv = call.svc.conversations.create(persona_id=mira.id)
        await call.hello(conv.id)
        assert call.of("ready")[0]["persona_name"] == "Mira"
        assert dict(call.session.blend.weights) == {"af_heart": 0.6, "bf_emma": 0.4}
        assert call.session.blend.speed == 1.1


@pytest.mark.anyio
async def test_call_with_the_builtin_keeps_the_settings_voice():
    call = Call(hello=False, settings={"voice": {"short_wait_ms": 0, "max_wait_ms": 250, "ack": True,
                                                 "default_blend": {"bf_emma": 1.0}}})
    async with call:
        await call.hello()
        assert call.of("ready")[0]["persona_name"] == "Aethel"
        assert dict(call.session.blend.weights) == {"bf_emma": 1.0}
