import io
import wave

import httpx
import numpy as np
from fastapi.testclient import TestClient

from aethel.app import create_app
from aethel.services import build_services
from aethel.voice import tts as tts_module
from aethel.voice.tts import Speech
from tests.fakes import FakeLocal, FakeProvider, factory_from


def client(tmp_path=None):
    svc = build_services(provider_factory=factory_from({"groq:g": FakeProvider()}), local_llm=FakeLocal(fail="none"))
    return TestClient(create_app(svc)), svc


class Engine:
    prefer_azure = False

    def __init__(self, speech=Speech(np.zeros(2400, dtype=np.int16).tobytes(), "pcm16", 24000, 0.1)):
        self.speech, self.calls = speech, []

    async def synthesize(self, text, blend, private):
        self.calls.append((text, blend, private))
        return self.speech


def test_status_without_credentials_is_local(monkeypatch):
    c, svc = client()
    with c:
        body = c.get("/api/voice/status").json()
    assert body["stt"]["name"] == "local:faster-whisper-small" and body["stt"]["azure"] is False
    assert body["tts"]["azure"] is False and body["last_turn_ms"] is None
    assert body["tts"]["kokoro"] == svc.voice.kokoro.available


def test_status_with_azure_credentials_says_so_and_private_mode_withdraws_it():
    c, svc = client()
    with c:
        c.post("/api/keys", json={"keys": {"azure_speech": "k"}})
        c.patch("/api/settings", json={"voice": {"azure_region": "uksouth"}})
        body = c.get("/api/voice/status").json()
        assert body["stt"] == {"name": "azure:uksouth", "azure": True, "loaded": True}
        assert body["tts"]["azure"] is True
        c.patch("/api/settings", json={"private_mode": True})
        body = c.get("/api/voice/status").json()
        assert body["stt"]["name"].startswith("local:") and body["tts"]["azure"] is False


def test_the_key_never_comes_back_in_any_voice_response():
    c, _ = client()
    with c:
        c.post("/api/keys", json={"keys": {"azure_speech": "super-secret-key"}})
        c.patch("/api/settings", json={"voice": {"azure_region": "uksouth"}})
        for path in ("/api/voice/status", "/api/voice/voices", "/api/settings", "/api/keys/status"):
            assert "super-secret-key" not in c.get(path).text


def test_voices_are_grouped_by_language():
    c, svc = client()
    if not svc.voice.kokoro.available:
        return
    with c:
        groups = c.get("/api/voice/voices").json()
    assert "af_heart" in groups["en-us"] and "bf_emma" in groups["en-gb"]


def test_preview_returns_a_wav_and_speaks_the_cleaned_text(monkeypatch):
    c, svc = client()
    engine = Engine()
    svc.voice.tts = lambda: engine
    svc.voice.kokoro.voices = lambda: ["af_heart", "bf_emma"]
    with c:
        r = c.post("/api/voice/preview", json={"blend": {"af_heart": 0.7, "bf_emma": 0.3}, "text": "Hello **there** [2]"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (24000, 1, 2400)
    text, blend, private = engine.calls[0]
    assert text == "Hello there" and dict(blend.weights) == {"af_heart": 0.7, "bf_emma": 0.3} and private is False


def test_preview_rejects_bad_blends_and_empty_text():
    c, svc = client()
    svc.voice.kokoro.voices = lambda: ["af_heart"]
    svc.voice.tts = lambda: Engine()
    with c:
        assert c.post("/api/voice/preview", json={"blend": {"zz_nobody": 1}, "text": "hi"}).status_code == 422
        assert c.post("/api/voice/preview", json={"blend": {"af_heart": -1}, "text": "hi"}).status_code == 422
        assert c.post("/api/voice/preview", json={"blend": {"af_heart": 1}, "text": "[3]"}).status_code == 422
        assert c.post("/api/voice/preview", json={"blend": {"af_heart": 1}, "text": "x" * 201}).status_code == 422


def test_preview_with_no_voice_available_is_503():
    c, svc = client()
    svc.voice.kokoro.voices = lambda: ["af_heart"]
    svc.voice.tts = lambda: Engine(speech=None)
    with c:
        assert c.post("/api/voice/preview", json={"blend": {"af_heart": 1}, "text": "hi"}).status_code == 503


def test_azure_check_reports_each_state(monkeypatch):
    c, svc = client()
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200 if request.headers["ocp-apim-subscription-key"] == "good" else 401, content=b"\x00\x00" * 100)

    real = httpx.Client
    monkeypatch.setattr(tts_module.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler)))
    with c:
        assert c.post("/api/voice/azure-check").json() == {"ok": False, "message": "Add the Azure Speech key and its region first."}
        c.patch("/api/settings", json={"voice": {"azure_region": "uksouth"}})
        c.post("/api/keys", json={"keys": {"azure_speech": "bad"}})
        r = c.post("/api/voice/azure-check").json()
        assert r["ok"] is False and "refused the key or region" in r["message"] and "bad" not in r["message"]
        c.post("/api/keys", json={"keys": {"azure_speech": "good"}})
        assert c.post("/api/voice/azure-check").json() == {"ok": True, "message": "Azure Speech works in uksouth."}
        c.patch("/api/settings", json={"private_mode": True})
        assert c.post("/api/voice/azure-check").json()["ok"] is False
    assert len(seen) == 2   # private mode and missing credentials sent nothing


def test_routes_need_the_token(monkeypatch):
    monkeypatch.setenv("AETHEL_DEV", "0")
    monkeypatch.setenv("AETHEL_TOKEN", "t0k")
    c, _ = client()
    with c:
        for path in ("/api/voice/status", "/api/voice/voices"):
            assert c.get(path).status_code == 401
        assert c.post("/api/voice/azure-check").status_code == 401


def test_system_one_end_of_turn_is_off_unless_switched_on():
    _, svc = client()
    assert svc.voice.eot().system1 is None
    svc.settings.update({"voice": {"system1_end_of_turn": True}})
    assert svc.voice.eot().system1 is svc.system1
