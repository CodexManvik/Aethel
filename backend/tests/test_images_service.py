import io
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from aethel.app import create_app
from aethel.hub import EventHub
from aethel.images.backend import ImageError, ImageResult
from aethel.images.compose import CONSISTENCY, compose
from aethel.images.service import ImageService
from aethel.images.tool import SOMETIMES_EVERY, FrequencyRule
from aethel.personas.model import Persona
from aethel.personas.store import PersonaStore
from aethel.providers.base import TextDelta
from aethel.services import build_services
from aethel.settings import SettingsService
from aethel.store.db import Database
from aethel.store.repos import ConversationRepo, MessageRepo
from tests.fakes import FakeLocal, ScriptedProvider, factory_from, tool_call


def _jpeg(colour=(200, 120, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (24, 32), colour).save(buf, "JPEG")
    return buf.getvalue()


class FakeBackend:
    name, cloud, max_references = "fake", True, 6

    def __init__(self, fail=None):
        self.requests, self.fail, self.ok = [], fail, True

    async def available(self):
        return self.ok, "" if self.ok else "off"

    async def generate(self, req):
        self.requests.append(req)
        if self.fail:
            raise self.fail
        return ImageResult(_jpeg(), "image/jpeg", "fake", "fake-1", 5)


class FakeIdentity:
    """Scores the generated pictures in turn; the anchor embedding is a fixed vector."""

    def __init__(self, scores, ready=True):
        self.scores, self._ready, self.compared = list(scores), ready, 0

    def ready(self):
        return self._ready, ""

    def embedding_of(self, data):
        return np.ones(4) / 2

    def compare(self, data, anchor):
        self.compared += 1
        return self.scores.pop(0)


class Hub:
    def __init__(self):
        self.events = []

    async def publish(self, ev):
        self.events.append(ev)


@pytest.fixture
def setup(tmp_path):
    db = Database(tmp_path / "t.db")
    settings = SettingsService(db)
    store = PersonaStore(tmp_path / "personas")
    mira = store.create(Persona(id="x", name="Mira", image_style="warm film photo"))
    folder = store.folder(mira.id)
    (folder / "portraits").mkdir()
    (folder / "portraits" / "anchor.jpg").write_bytes(_jpeg())
    (folder / "portraits" / "sheet-34.jpg").write_bytes(_jpeg((10, 10, 10)))
    mira = store.update(mira.id, {"anchors": ["portraits/anchor.jpg"], "sheet": ["portraits/sheet-34.jpg"]})
    return db, settings, store, mira


def _service(setup, backend, identity, messages=None):
    db, settings, store, mira = setup
    hub = Hub()
    return ImageService(personas=store, backends={"gemini": backend}, settings=settings, identity=identity, hub=hub,
                        messages=messages), hub


def test_compose_order_and_omissions():
    p = Persona(id="m", name="M", image_style="warm film photo.")
    assert compose(p, "at the beach", "half-body", "smiling") == (
        f"warm film photo. at the beach. Framing: half-body. Expression: smiling. {CONSISTENCY}")
    assert compose(Persona(id="m", name="M"), "  reading   at home. ", "sideways", None, references=False) == "reading at home."


@pytest.mark.anyio
async def test_a_pass_is_saved_with_references_and_pooled(setup):
    backend, identity = FakeBackend(), FakeIdentity([0.8])
    svc, hub = _service(setup, backend, identity)
    got = await svc.generate(persona=setup[3], scene="at the beach", reason="chat")
    assert got["ok"] and got["score"] == 0.8
    assert [type(e).__name__ for e in hub.events] == ["ImagePending", "ImageReady"]
    assert len(backend.requests[0].references) == 2 and backend.requests[0].prompt.startswith("warm film photo. at the beach")
    meta = svc.gallery(setup[3].id)[0]
    assert meta["reference"] is True and meta["tries"] == 1 and meta["low"] is False
    assert (setup[2].folder(setup[3].id) / "anchor.npy").exists()  # the anchor embedding is cached


@pytest.mark.anyio
async def test_a_fail_then_a_pass_keeps_the_second(setup):
    svc, _ = _service(setup, FakeBackend(), FakeIdentity([0.2, 0.7]))
    got = await svc.generate(persona=setup[3], scene="café")
    meta = svc.gallery(setup[3].id)[0]
    assert got["score"] == 0.7 and meta["tries"] == 2 and not meta["low"]


@pytest.mark.anyio
async def test_two_fails_keep_the_better_and_mark_it_low(setup):
    svc, hub = _service(setup, FakeBackend(), FakeIdentity([0.3, 0.1]))
    got = await svc.generate(persona=setup[3], scene="rain at night")
    meta = svc.gallery(setup[3].id)[0]
    assert got["score"] == 0.3 and meta["low"] is True and meta["reference"] is False
    assert hub.events[-1].identity_score == 0.3


@pytest.mark.anyio
async def test_no_face_means_no_check_and_no_second_try(setup):
    backend = FakeBackend()
    svc, _ = _service(setup, backend, FakeIdentity([None]))
    got = await svc.generate(persona=setup[3], scene="the view from the hill")
    assert got["score"] is None and len(backend.requests) == 1


@pytest.mark.anyio
async def test_without_face_models_nothing_is_checked(setup):
    identity = FakeIdentity([], ready=False)
    svc, _ = _service(setup, FakeBackend(), identity)
    assert (await svc.generate(persona=setup[3], scene="x"))["score"] is None and identity.compared == 0


@pytest.mark.anyio
async def test_a_failure_is_published_and_can_be_retried(setup):
    backend = FakeBackend(fail=ImageError("Gemini is busy (429).", retryable=True))
    db = setup[0]
    conv = ConversationRepo(db).create(persona_id=setup[3].id)
    messages = MessageRepo(db)
    reply = messages.add(conv.id, "assistant", "here")
    svc, hub = _service(setup, backend, FakeIdentity([0.9]), messages=messages)
    image_id = await svc.start(persona=setup[3], scene="x", conversation_id=conv.id, message_id=reply.id)
    await svc.wait_idle()
    failed = hub.events[-1]
    assert type(failed).__name__ == "ImageFailed" and failed.retryable and "busy" in failed.message
    assert messages.get(reply.id).meta["images"][0]["status"] == "failed"
    backend.fail = None
    await svc.retry(image_id)
    await svc.wait_idle()
    assert type(hub.events[-1]).__name__ == "ImageReady"
    assert messages.get(reply.id).meta["images"] == [
        {"id": image_id, "persona_id": "mira", "status": "ready", "score": 0.9, "caption": "x"}]


@pytest.mark.anyio
async def test_the_reference_pool_is_capped(setup):
    svc, _ = _service(setup, FakeBackend(), FakeIdentity([0.9] * 8))
    for _ in range(5):
        await svc.generate(persona=setup[3], scene="x")
    pooled = [m for m in svc.gallery(setup[3].id) if m["reference"]]
    assert len(pooled) == 4  # anchor + sheet + 4 = 6
    unpooled = next(m for m in svc.gallery(setup[3].id) if not m["reference"])
    with pytest.raises(ValueError):
        svc.set_reference(setup[3].id, unpooled["id"], True)
    svc.set_reference(setup[3].id, pooled[0]["id"], False)
    assert svc.set_reference(setup[3].id, unpooled["id"], True)["reference"] is True


@pytest.mark.anyio
async def test_a_path_is_never_an_image_id(setup):
    svc, _ = _service(setup, FakeBackend(), FakeIdentity([]))
    for bad in ("..\\victim", "../x", "a" * 16 + "/..", "ABCDEF0123456789"):
        with pytest.raises(KeyError):
            svc.set_reference(setup[3].id, bad, True)


class FlakyBackend(FakeBackend):
    """The first picture works, the second try fails."""

    async def generate(self, req):
        if self.requests:
            self.requests.append(req)
            raise ImageError("busy", retryable=True)
        return await super().generate(req)


@pytest.mark.anyio
async def test_a_failed_second_try_keeps_the_first_picture(setup):
    svc, hub = _service(setup, FlakyBackend(), FakeIdentity([0.1]))
    got = await svc.generate(persona=setup[3], scene="x")
    assert got["ok"] and got["score"] == 0.1 and svc.gallery(setup[3].id)[0]["low"] is True


class BrokenIdentity(FakeIdentity):
    def compare(self, data, anchor):
        raise RuntimeError("onnx went away")


@pytest.mark.anyio
async def test_a_broken_face_check_never_costs_the_picture(setup):
    svc, _ = _service(setup, FakeBackend(), BrokenIdentity([]))
    got = await svc.generate(persona=setup[3], scene="x")
    assert got["ok"] and got["score"] is None


class SlowBackend(FakeBackend):
    def __init__(self, store, pid):
        super().__init__()
        self.store, self.pid = store, pid

    async def generate(self, req):
        self.store.delete(self.pid)  # the persona goes while its photo is being drawn
        return await super().generate(req)


@pytest.mark.anyio
async def test_a_persona_deleted_mid_photo_isnt_recreated(setup):
    store, mira = setup[2], setup[3]
    svc, hub = _service(setup, SlowBackend(store, mira.id), FakeIdentity([0.9]))
    await svc.start(persona=mira, scene="x")
    await svc.wait_idle()
    assert type(hub.events[-1]).__name__ == "ImageFailed" and not (store.root / mira.id).exists()


@pytest.mark.anyio
async def test_a_photo_drawn_on_this_pc_waits_for_its_reply(setup):
    order = []
    local = FakeBackend()
    local.cloud = False

    async def reply_done(message_id):
        order.append(f"reply {message_id} done")

    svc, _ = _service(setup, local, FakeIdentity([0.9]))
    svc.reply_done = reply_done
    original = local.generate

    async def generate(req):
        order.append("drawing")
        return await original(req)
    local.generate = generate
    await svc.generate(persona=setup[3], scene="x", message_id="m1")
    assert order == ["reply m1 done", "drawing"]


def test_photos_cut_off_by_a_restart_are_marked_failed(setup):
    db = setup[0]
    conv = ConversationRepo(db).create(persona_id=setup[3].id)
    messages = MessageRepo(db)
    m = messages.add(conv.id, "assistant", "here", meta={"images": [{"id": "a", "status": "pending"},
                                                                   {"id": "b", "status": "ready"}]})
    assert messages.fail_stale_images() == 1
    assert messages.get(m.id).meta["images"] == [{"id": "a", "status": "failed", "stale": True},
                                                 {"id": "b", "status": "ready"}]


class FakeS1:
    def __init__(self, p):
        self.p = p

    async def ask(self, state, questions, purpose):
        return None if self.p is None else {"picture": {"noul": self.p}}


@pytest.mark.anyio
async def test_frequency_rule(setup):
    db, settings, store, mira = setup
    conv = ConversationRepo(db).create(persona_id=mira.id)
    messages = MessageRepo(db)
    rule = lambda p: FrequencyRule(system1=FakeS1(p), messages=messages, settings=settings)  # noqa: E731
    never = mira.model_copy(update={"image_frequency": "never"})
    asked = mira.model_copy(update={"image_frequency": "on_request"})
    sometimes = mira.model_copy(update={"image_frequency": "sometimes"})
    assert not await rule(0.99).allowed(never, conv.id, "send a pic")
    assert await rule(0.9).allowed(asked, conv.id, "can I see you?")
    assert not await rule(0.1).allowed(asked, conv.id, "how was work")
    assert await rule(None).allowed(asked, conv.id, "send me a selfie")   # System 1 off: the obvious wordings
    assert not await rule(None).allowed(asked, conv.id, "how was work")
    assert not await rule(0.1).allowed(asked.model_copy(update={"anchors": []}), conv.id, "x")  # no face yet
    for i in range(SOMETIMES_EVERY):
        messages.add(conv.id, "assistant", f"reply {i}")
    messages.add(conv.id, "assistant", "")  # the reply being written
    assert await rule(0.1).allowed(sometimes, conv.id, "how was work")
    messages.add(conv.id, "assistant", "with a photo", meta={"images": [{"id": "i"}]})
    messages.add(conv.id, "assistant", "")
    assert not await rule(0.1).allowed(sometimes, conv.id, "how was work")


def _chat(provider, backend_ok=True, private=False):
    svc = build_services(provider_factory=factory_from({"groq:g": provider, "local:local": provider}),
                         local_llm=FakeLocal(up=True))
    svc.keys.set_many({"groq": "k"})
    svc.settings.update({"roles": {"chat": [{"provider": "groq", "model": "g"}]}, "private_mode": private})
    backend = FakeBackend()
    backend.ok = backend_ok
    svc.images.backends = {"gemini": backend, "local": FakeBackend(), "openrouter": FakeBackend()}
    svc.images.backends["local"].cloud, svc.images.backends["local"].ok = False, False
    svc.images.backends["openrouter"].ok = False
    svc.images.identity = FakeIdentity([0.9])
    svc.chat.image_rule.system1 = FakeS1(0.95)
    mira = svc.personas.create(Persona(id="x", name="Mira", image_style="film"))
    folder = svc.personas.folder(mira.id)
    (folder / "portraits").mkdir()
    (folder / "portraits" / "anchor.jpg").write_bytes(_jpeg())
    svc.personas.update(mira.id, {"anchors": ["portraits/anchor.jpg"]})
    return TestClient(create_app(svc)), svc


def _events_until_ready(ws, conv_id, text):
    ws.send_json({"type": "user_message", "conversation_id": conv_id, "text": text})
    events = []
    while not any(e["type"] in ("image_ready", "image_failed") for e in events) or \
            not any(e["type"] == "message_end" for e in events):
        events.append(ws.receive_json())
    return events


def test_chat_sends_a_photo_while_the_words_stream():
    provider = ScriptedProvider([
        [tool_call("image_generate", scene="on the beach at sunset", framing="half-body", expression="smiling")],
        [TextDelta("Here's me at the beach!")]])
    client, svc = _chat(provider)
    with client:
        conv = client.post("/api/conversations", json={"persona_id": "mira"}).json()
        with client.websocket_connect("/ws/session") as ws:
            events = _events_until_ready(ws, conv["id"], "send me a picture of you at the beach")
    types = [e["type"] for e in events]
    assert types.index("image_pending") < types.index("token")
    pending = next(e for e in events if e["type"] == "image_pending")
    assert pending["caption"] == "on the beach at sunset" and pending["persona_id"] == "mira"
    assert provider.tools_seen[0] == ["image_generate"]
    reply = svc.messages.list(conv["id"])[-1]
    assert reply.content == "Here's me at the beach!"
    assert reply.meta["images"][0]["status"] == "ready" and reply.meta["images"][0]["score"] == 0.9
    sent = provider.calls[1][-1].content
    assert "A photo is on its way" in sent


@pytest.mark.parametrize("backend_ok,private", [(False, False), (True, True)])
def test_no_tool_without_a_backend_or_in_private_mode_without_local(backend_ok, private):
    provider = ScriptedProvider([[TextDelta("hi")]])
    client, _ = _chat(provider, backend_ok=backend_ok, private=private)
    with client:
        conv = client.post("/api/conversations", json={"persona_id": "mira"}).json()
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": "send a pic"})
            while ws.receive_json()["type"] != "message_end":
                pass
    assert provider.tools_seen == [[]]
