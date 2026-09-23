import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aethel.app import create_app
from aethel.chat.persona import system_prompt
from aethel.chat.service import make_title
from aethel.services import build_services
from tests.fakes import FakeLocal, FakeProvider, factory_from


def _client(providers, keys=None, **settings_patch):
    services = build_services(provider_factory=factory_from(providers), local_llm=FakeLocal(fail="no local model"))
    services.keys.set_many(keys if keys is not None else {"groq": "k"})
    services.settings.update({"roles": {"chat": [{"provider": "groq", "model": "g"}]}, **settings_patch})
    return TestClient(create_app(services)), services


def _receive_until_end(ws):
    events = []
    while True:
        ev = ws.receive_json()
        events.append(ev)
        if ev["type"] == "message_end":
            return events


def test_turn_streams_persists_and_titles():
    provider = FakeProvider(chunks=["Hel", "lo!"])
    client, svc = _client({"groq:g": provider})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": "hi there", "client_id": "c1"})
            events = _receive_until_end(ws)
    types = [e["type"] for e in events]
    assert types == ["message_start", "conversation_updated", "token", "token", "message_end"]
    start = events[0]
    assert start["client_id"] == "c1"
    assert events[1]["title"] == "hi there"
    assert events[-1]["status"] == "complete"
    stored = [(m.role, m.content, m.status) for m in svc.messages.list(conv["id"])]
    assert stored == [("user", "hi there", "complete"), ("assistant", "Hello!", "complete")]
    sent = provider.calls[0]
    assert sent[0].role == "system" and "Aethel" in sent[0].content
    assert [(m.role, m.content) for m in sent[1:]] == [("user", "hi there")]


def test_history_is_included_on_second_turn():
    provider = FakeProvider(chunks=["ok"])
    client, _ = _client({"groq:g": provider})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            for text in ("first", "second"):
                ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": text})
                _receive_until_end(ws)
    assert [(m.role, m.content) for m in provider.calls[1][1:]] == [
        ("user", "first"), ("assistant", "ok"), ("user", "second")
    ]


def test_no_provider_emits_error_and_marks_message():
    client, svc = _client({}, keys={})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": "hi"})
            events = _receive_until_end(ws)
    error = next(e for e in events if e["type"] == "error")
    assert error["code"] == "no_provider" and "no API key" in error["message"]
    assert events[-1]["status"] == "error"
    assert svc.messages.list(conv["id"])[-1].status == "error"


def test_stop_generation_keeps_partial_text():
    client, svc = _client({"groq:g": FakeProvider(chunks=["a", "b", "c"], delay=0.3)})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": "go"})
            start = ws.receive_json()
            ws.receive_json()  # conversation_updated
            assert ws.receive_json() == {"type": "token", "message_id": start["message_id"], "text": "a"}
            ws.send_json({"type": "stop_generation", "message_id": start["message_id"]})
            end = ws.receive_json()
    assert end == {"type": "message_end", "message_id": start["message_id"], "status": "stopped"}
    assert svc.messages.list(conv["id"])[-1].content == "a"


def test_unknown_conversation_and_bad_json_are_reported():
    client, _ = _client({"groq:g": FakeProvider()})
    with client:
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text("{not json")
            assert ws.receive_json()["code"] == "bad_request"
            ws.send_json({"type": "user_message", "conversation_id": "missing", "text": "hi"})
            assert ws.receive_json()["code"] == "bad_request"


def test_websocket_rejects_bad_token(monkeypatch):
    monkeypatch.setenv("AETHEL_DEV", "0")
    monkeypatch.setenv("AETHEL_TOKEN", "right")
    client, _ = _client({"groq:g": FakeProvider()})
    with client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/session?token=wrong") as ws:
                ws.receive_json()
        with client.websocket_connect("/ws/session?token=right"):
            pass


def test_make_title_and_prompt():
    assert make_title("  hello\n  world  ") == "hello world"
    long = make_title("x" * 100)
    assert len(long) == 48 and long.endswith("…")
    assert "Current local time" in system_prompt()


@pytest.mark.anyio
async def test_exception_before_stream_still_persists_error_and_ends_message():
    """A failure in the pre-stream section (e.g. rename hitting a bad DB) must
    still be caught by the turn's try/finally: no row stuck "streaming", no
    leaked _active entry, and a message_end still reaches the client."""
    services = build_services(provider_factory=factory_from({"groq:g": FakeProvider()}),
                               local_llm=FakeLocal(fail="no local model"))
    services.keys.set_many({"groq": "k"})
    services.settings.update({"roles": {"chat": [{"provider": "groq", "model": "g"}]}})
    conv = services.conversations.create()

    def boom(conv_id, title):
        raise RuntimeError("db exploded")

    services.conversations.rename = boom

    events = []

    async def emit(ev):
        events.append(ev)

    from aethel.api.events import UserMessage
    await services.chat._turn(UserMessage(conversation_id=conv.id, text="hi"), emit)

    types = [e.type for e in events]
    assert types == ["message_start", "error", "message_end"]
    assert events[-1].status == "error"
    stored = services.messages.list(conv.id)
    assert stored[-1].status == "error"
    assert services.chat._active == {}
    services.close()


@pytest.mark.anyio
async def test_shutdown_times_out_and_cancels_slow_turns():
    """shutdown(timeout=...) must not hang forever waiting on a slow turn: once
    the timeout elapses it cancels what's left (persisting "stopped") and
    returns promptly."""
    provider = FakeProvider(chunks=["a", "b", "c"], delay=5.0)
    services = build_services(provider_factory=factory_from({"groq:g": provider}),
                               local_llm=FakeLocal(fail="no local model"))
    services.keys.set_many({"groq": "k"})
    services.settings.update({"roles": {"chat": [{"provider": "groq", "model": "g"}]}})
    conv = services.conversations.create()

    events = []

    async def emit(ev):
        events.append(ev)

    from aethel.api.events import UserMessage
    services.chat.start_turn(UserMessage(conversation_id=conv.id, text="go"), emit)
    await asyncio.sleep(0.05)  # let the turn register itself in _active/_tasks

    started = asyncio.get_event_loop().time()
    await services.chat.shutdown(timeout=0.2)
    elapsed = asyncio.get_event_loop().time() - started

    assert elapsed < 4.0  # returned promptly, not after the provider's 5s delay
    assert services.chat._tasks == set()
    stored = services.messages.list(conv.id)
    assert stored[-1].status == "stopped"
    services.close()
