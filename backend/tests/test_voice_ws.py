import json
import struct

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aethel.app import create_app
from aethel.services import build_services
from tests.fakes import FakeLocal, FakeProvider, factory_from
from tests.test_voice_session import SENTENCES, FakeEOT, FakeSTT, FakeTTS


def client(**patch):
    svc = build_services(provider_factory=factory_from({"groq:g": FakeProvider(chunks=SENTENCES[:2])}),
                         local_llm=FakeLocal(fail="none"))
    svc.keys.set_many({"groq": "k"})
    svc.settings.update({"roles": {"chat": [{"provider": "groq", "model": "g"}]},
                         "voice": {"short_wait_ms": 0, "max_wait_ms": 250, "ack": False}, **patch})
    svc.voice.stt, svc.voice.tts, svc.voice.eot = (lambda: FakeSTT()), (lambda: FakeTTS()), (lambda: FakeEOT())
    return TestClient(create_app(svc)), svc


def frames_until(ws, kind):
    """JSON messages and binary frames, up to and including the first JSON message of `kind`."""
    got = []
    for _ in range(50):
        message = ws.receive()
        if message.get("bytes") is not None:
            got.append(("audio", struct.unpack("<i", message["bytes"][:4])[0], message["bytes"][4:]))
            continue
        payload = json.loads(message["text"])
        got.append(("json", payload))
        if payload["type"] == kind:
            return got
    raise AssertionError(f"no {kind} in 50 frames: {got}")


def test_a_call_over_the_socket():
    c, svc = client()
    with c:
        conv = c.post("/api/conversations", json={}).json()
        with c.websocket_connect("/ws/voice") as ws:
            ws.send_text(json.dumps({"type": "hello", "conversation_id": conv["id"]}))
            ready = frames_until(ws, "ready")[-1][1]
            assert ready["stt"] == "fake:stt"
            ws.send_text('{"type": "speech_start"}')
            ws.send_text('{"type": "speech_end", "duration_ms": 900}')
            ws.send_bytes(b"what time does it open")
            frames = frames_until(ws, "turn_end")
            ws.send_text('{"type": "end_call"}')
    json_types = [f[1]["type"] for f in frames if f[0] == "json"]
    assert json_types == ["caption", "thinking", "tts_meta", "tts_meta", "caption", "turn_end"]
    audio = [f for f in frames if f[0] == "audio"]
    assert [a[1] for a in audio] == [0, 1] and audio[0][2] == SENTENCES[0].strip().encode()
    assert [m.meta.get("modality") for m in svc.messages.list(conv["id"])] == ["voice", "voice"]


def test_a_second_socket_on_the_same_conversation_is_refused_and_the_first_is_untouched():
    c, svc = client()
    with c:
        conv = c.post("/api/conversations", json={}).json()
        with c.websocket_connect("/ws/voice") as first:
            first.send_text(json.dumps({"type": "hello", "conversation_id": conv["id"]}))
            frames_until(first, "ready")
            with c.websocket_connect("/ws/voice") as second:
                second.send_text(json.dumps({"type": "hello", "conversation_id": conv["id"]}))
                error = frames_until(second, "voice_error")[-1][1]
            assert error == {"type": "voice_error", "message": "This conversation is already in a call.", "fatal": True}
            first.send_text('{"type": "speech_start"}')   # still alive
            first.send_text('{"type": "end_call"}')
    assert conv["id"] not in svc.voice.calls


def test_the_foreign_origin_is_refused():
    c, _ = client()
    with c:
        with pytest.raises(WebSocketDisconnect) as exc:
            with c.websocket_connect("/ws/voice", headers={"origin": "https://evil.example"}) as ws:
                ws.receive()
        assert exc.value.code == 4403


def test_the_token_is_required(monkeypatch):
    monkeypatch.setenv("AETHEL_DEV", "0")
    monkeypatch.setenv("AETHEL_TOKEN", "t0k")
    c, _ = client()
    with c:
        with pytest.raises(WebSocketDisconnect) as exc:
            with c.websocket_connect("/ws/voice") as ws:
                ws.receive()
        assert exc.value.code == 4401
        with c.websocket_connect("/ws/voice?token=t0k") as ws:
            ws.send_text("{}")
            assert json.loads(ws.receive()["text"])["type"] == "voice_error"  # connected: the hello was missing
