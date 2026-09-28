import asyncio
import json

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
            msg = ws.receive_json()
            assert msg["code"] == "bad_request"
            assert msg["conversation_id"] == "missing"


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

    async def collect(payload):
        events.append(json.loads(payload))

    services.hub.subscribe(collect)

    from aethel.protocol import UserMessage
    await services.chat._turn(UserMessage(conversation_id=conv.id, text="hi"))

    types = [e["type"] for e in events]
    assert types == ["message_start", "error", "message_end"]
    assert events[-1]["status"] == "error"
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

    from aethel.protocol import UserMessage
    services.chat.start_turn(UserMessage(conversation_id=conv.id, text="go"))
    await asyncio.sleep(0.05)  # let the turn register itself in _active/_tasks

    started = asyncio.get_event_loop().time()
    await services.chat.shutdown(timeout=0.2)
    elapsed = asyncio.get_event_loop().time() - started

    assert elapsed < 4.0  # returned promptly, not after the provider's 5s delay
    assert services.chat._tasks == set()
    stored = services.messages.list(conv.id)
    assert stored[-1].status == "stopped"
    services.close()


def test_build_services_reconciles_rows_left_streaming_by_a_crash():
    first = build_services(local_llm=FakeLocal())
    conv = first.conversations.create()
    cut = first.messages.add(conv.id, "assistant", "half a rep", status="streaming")
    first.close()
    second = build_services(local_llm=FakeLocal())
    [stored] = second.messages.list(conv.id)
    assert (stored.id, stored.status, stored.content) == (cut.id, "stopped", "half a rep")
    second.close()


def test_stop_on_orphaned_streaming_row_ends_it():
    """A row left "streaming" with no task behind it (e.g. the reply was cut
    off mid-stream) must not block the conversation: stop marks it stopped and
    tells the client."""
    client, svc = _client({"groq:g": FakeProvider()})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        orphan = svc.messages.add(conv["id"], "assistant", "half", status="streaming")
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "stop_generation", "message_id": orphan.id})
            # Sentinel: always answered, so a missing message_end fails fast instead of hanging.
            ws.send_json({"type": "user_message", "conversation_id": "missing", "text": "x"})
            assert ws.receive_json() == {"type": "message_end", "message_id": orphan.id, "status": "stopped"}
            assert ws.receive_json()["code"] == "bad_request"
    [stored] = svc.messages.list(conv["id"])
    assert (stored.status, stored.content) == ("stopped", "half")


def test_stop_on_unknown_or_finished_message_emits_nothing():
    client, svc = _client({"groq:g": FakeProvider(chunks=["ok"])})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        done = svc.messages.add(conv["id"], "assistant", "x", status="complete")
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "stop_generation", "message_id": "missing"})
            ws.send_json({"type": "stop_generation", "message_id": done.id})
            # The next thing on the wire is the reply to this, not a stray message_end.
            ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": "hi"})
            assert ws.receive_json()["type"] == "message_start"
            _receive_until_end(ws)
    assert svc.messages.list(conv["id"])[0].status == "complete"


def test_back_to_back_messages_run_as_ordered_turns():
    """Two user_messages sent without waiting are serialised: turn 2 starts
    only after turn 1 ends, sees reply 1 in its context, and the DB keeps
    user1, assistant1, user2, assistant2."""
    provider = FakeProvider(chunks=["r", "e", "p"], delay=0.05)
    client, svc = _client({"groq:g": provider})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": "one", "client_id": "c1"})
            ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": "two", "client_id": "c2"})
            first = _receive_until_end(ws)
            second = _receive_until_end(ws)
    assert first[0]["client_id"] == "c1" and second[0]["client_id"] == "c2"
    assert {e.get("message_id") for e in first if e["type"] in ("token", "message_end")} == {first[0]["message_id"]}
    assert {e.get("message_id") for e in second if e["type"] in ("token", "message_end")} == {second[0]["message_id"]}
    assert first[-1]["status"] == second[-1]["status"] == "complete"
    stored = [(m.role, m.content) for m in svc.messages.list(conv["id"])]
    assert stored == [("user", "one"), ("assistant", "rep"), ("user", "two"), ("assistant", "rep")]
    assert [(m.role, m.content) for m in provider.calls[1][1:]] == [
        ("user", "one"), ("assistant", "rep"), ("user", "two")
    ]
    assert svc.chat._locks == {}  # per-conversation locks don't accumulate


def test_websocket_rejects_foreign_origin_but_allows_known_or_missing():
    from aethel.app import ALLOWED_ORIGINS

    client, _ = _client({"groq:g": FakeProvider()})
    with client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/session", headers={"origin": "https://evil.example"}) as ws:
                ws.send_text("{not json")  # if wrongly accepted, get an answer rather than hang
                ws.receive_json()
        assert exc.value.code == 4403
        for headers in ({"origin": ALLOWED_ORIGINS[0]}, {"origin": "tauri://localhost"}, {}):
            with client.websocket_connect("/ws/session", headers=headers) as ws:
                ws.send_text("{not json")
                assert ws.receive_json()["code"] == "bad_request"  # accepted and serving


def test_provider_switch_is_scoped_to_its_message():
    from tests.fakes import retryable

    services = build_services(
        provider_factory=factory_from({
            "groq:g": FakeProvider(label="groq:g", error=retryable()),
            "openrouter:o": FakeProvider(label="openrouter:o", chunks=["ok"]),
        }),
        local_llm=FakeLocal(fail="no local model"),
    )
    services.keys.set_many({"groq": "a", "openrouter": "b"})
    services.settings.update({"roles": {"chat": [{"provider": "groq", "model": "g"},
                                                  {"provider": "openrouter", "model": "o"}]}})
    client = TestClient(create_app(services))
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": "hi"})
            events = _receive_until_end(ws)
    start = next(e for e in events if e["type"] == "message_start")
    switch = next(e for e in events if e["type"] == "provider_switched")
    assert switch["message_id"] == start["message_id"]
    assert switch["task_id"] is None


def test_every_open_socket_receives_the_turn():
    client, _ = _client({"groq:g": FakeProvider(chunks=["a", "b"])})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws1, client.websocket_connect("/ws/session") as ws2:
            ws1.send_json({"type": "user_message", "conversation_id": conv["id"], "text": "hi"})
            e1 = _receive_until_end(ws1)
            e2 = _receive_until_end(ws2)
    assert [e["type"] for e in e1] == [e["type"] for e in e2]


def test_messages_endpoint_overlays_partial_text_while_streaming():
    client, svc = _client({"groq:g": FakeProvider(chunks=["Hel", "lo", " there"], delay=0.3)})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": "hi"})
            start = ws.receive_json()
            ws.receive_json()  # conversation_updated
            ws.receive_json()  # token "Hel"
            msgs = client.get(f"/api/conversations/{conv['id']}/messages").json()
            streaming = next(m for m in msgs if m["id"] == start["message_id"])
            assert streaming["status"] == "streaming"
            assert streaming["content"].startswith("Hel")
            _receive_until_end(ws)


def test_reply_continues_on_a_new_socket_after_reconnect():
    client, svc = _client({"groq:g": FakeProvider(chunks=["a", "b", "c"], delay=0.3)})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": "hi"})
            start = ws.receive_json()
        with client.websocket_connect("/ws/session") as ws2:
            events = _receive_until_end(ws2)
    assert events[-1] == {"type": "message_end", "message_id": start["message_id"], "status": "complete"}
    assert svc.messages.list(conv["id"])[-1].content == "abc"


@pytest.mark.anyio
async def test_foreign_cancellation_propagates_but_still_persists():
    import asyncio
    from aethel.protocol import UserMessage

    services = build_services(provider_factory=factory_from({"groq:g": FakeProvider(chunks=["a", "b"], delay=1)}),
                              local_llm=FakeLocal())
    services.keys.set_many({"groq": "k"})
    services.settings.update({"roles": {"chat": [{"provider": "groq", "model": "g"}]}})
    conv = services.conversations.create()
    services.chat.start_turn(UserMessage(conversation_id=conv.id, text="hi"))
    await asyncio.sleep(0.1)
    [task] = list(services.chat._tasks)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert services.messages.list(conv.id)[-1].status == "stopped"
    services.close()
