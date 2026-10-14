import os

import httpx
import numpy as np
import pytest

from aethel.keys import KeyStore
from aethel.settings import AppSettings
from aethel.voice.azure import AzureSpeech
from aethel.voice.tts import TTS, AzureTTS, KokoroTTS, Speech, TTSError, VoiceBlend, make_tts, speakable

pytestmark = pytest.mark.anyio
VOICES = {"af_heart", "bf_emma", "am_adam"}


def test_blend_parse_normalises_and_clamps_speed():
    blend = VoiceBlend.parse({"bf_emma": 1.0, "af_heart": 3.0, "speed": 5}, VOICES)
    assert blend.weights == (("af_heart", 0.75), ("bf_emma", 0.25))  # the main voice first
    assert blend.speed == 2.0
    assert VoiceBlend.parse({"af_heart": 1, "speed": 0.1}).speed == 0.5
    assert VoiceBlend.parse({"af_heart": 0, "bf_emma": 2}).weights == (("bf_emma", 1.0),)


def test_blend_parse_rejects_bad_input():
    with pytest.raises(ValueError, match="unknown voice"):
        VoiceBlend.parse({"zz_nobody": 1}, VOICES)
    with pytest.raises(ValueError, match="unknown voice"):
        VoiceBlend.parse({"../../etc": 1})  # not even a voice-shaped name
    with pytest.raises(ValueError, match="negative"):
        VoiceBlend.parse({"af_heart": -1})
    with pytest.raises(ValueError, match="at least one"):
        VoiceBlend.parse({"speed": 1.0})
    with pytest.raises(ValueError):
        VoiceBlend.parse({"af_heart": float("nan")})


def test_speakable():
    assert speakable("**Bold** and _soft_, see `code`.") == "Bold and soft, see code."
    assert speakable("Read [the guide](https://example.com/x) now") == "Read the guide now"
    assert speakable("Open https://example.com/a?b=1 or www.example.org today") == "Open a link or a link today"
    assert speakable("It opens at nine [3] and closes at five [1, 2].") == "It opens at nine and closes at five ."
    assert speakable("Run this:\n```py\nprint(1)\n```\nthen stop") == "Run this: (code) then stop"
    assert speakable("Nice 😀 work 👍🏽!") == "Nice work !"
    assert speakable("# Heading\n- one\n- two\n1. three\n> quote") == "Heading one two three quote"
    assert speakable("  \n ") == ""


class FakeKokoroModel:
    def __init__(self):
        self.calls = []

    def get_voices(self):
        return list(VOICES)

    def get_voice_style(self, name):
        return np.full((4, 1, 3), {"af_heart": 1.0, "bf_emma": 3.0, "am_adam": 0.0}[name], dtype=np.float32)

    def create(self, text, voice, speed=1.0, lang="en-us"):
        self.calls.append((text, voice, speed, lang))
        return np.linspace(-0.5, 0.5, 2400, dtype=np.float32), 24000


def fake_kokoro(tmp_path):
    (tmp_path / "kokoro-v1.0.onnx").write_bytes(b"x")
    (tmp_path / "voices-v1.0.bin").write_bytes(b"x")
    k = KokoroTTS(tmp_path)
    k._kokoro = FakeKokoroModel()
    return k


def test_kokoro_style_is_the_weighted_sum_and_cached(tmp_path):
    k = fake_kokoro(tmp_path)
    blend = VoiceBlend.parse({"af_heart": 0.75, "bf_emma": 0.25})
    style = k.style(blend)
    assert np.allclose(style, 0.75 * 1.0 + 0.25 * 3.0) and style.dtype == np.float32
    assert k.style(blend) is style
    assert k.voices() == sorted(VOICES)


def test_kokoro_synthesize_passes_style_speed_and_the_main_voices_language(tmp_path):
    k = fake_kokoro(tmp_path)
    speech = k.synthesize("Hello.", VoiceBlend.parse({"bf_emma": 1.0, "speed": 1.2}))
    text, voice, speed, lang = k._kokoro.calls[0]
    assert (text, speed, lang) == ("Hello.", 1.2, "en-gb") and voice.shape == (4, 1, 3)
    assert speech.codec == "pcm16" and speech.sample_rate == 24000 and speech.duration_s == pytest.approx(0.1)
    pcm = np.frombuffer(speech.audio, dtype=np.int16)
    assert len(pcm) == 2400 and pcm[0] < -16000 and pcm[-1] > 16000


def test_kokoro_without_model_files_says_so(tmp_path):
    with pytest.raises(TTSError, match="missing"):
        KokoroTTS(tmp_path).synthesize("Hi.", VoiceBlend.parse({"af_heart": 1}))


def azure_tts(handler):
    return AzureTTS(AzureSpeech("secret-key", "uksouth"), "en-GB-SoniaNeural",
                    httpx.Client(transport=httpx.MockTransport(handler)))


def test_azure_request_is_ssml_with_raw_pcm_and_the_text_escaped():
    seen = {}

    def handler(request):
        seen["url"], seen["headers"], seen["body"] = str(request.url), request.headers, request.content.decode()
        return httpx.Response(200, content=b"\x01\x00" * 4800)

    speech = azure_tts(handler).synthesize('Tom & "Jerry" <b>', VoiceBlend.parse({"af_heart": 1, "speed": 1.25}))
    assert seen["url"] == "https://uksouth.tts.speech.microsoft.com/cognitiveservices/v1"
    assert seen["headers"]["ocp-apim-subscription-key"] == "secret-key"
    assert seen["headers"]["x-microsoft-outputformat"] == "raw-24khz-16bit-mono-pcm"
    assert seen["headers"]["content-type"] == "application/ssml+xml"
    assert "xml:lang='en-GB'" in seen["body"] and "<voice name='en-GB-SoniaNeural'>" in seen["body"]
    assert "rate='+25%'" in seen["body"] and "Tom &amp; \"Jerry\" &lt;b&gt;" in seen["body"]
    assert (speech.codec, speech.sample_rate, speech.duration_s) == ("pcm16", 24000, pytest.approx(0.2))


@pytest.mark.parametrize("code,fragment", [(401, "refused"), (500, "answered 500")])
def test_azure_errors(code, fragment):
    with pytest.raises(TTSError, match=fragment) as err:
        azure_tts(lambda r: httpx.Response(code, text="secret-key")).synthesize("Hi.")
    assert "secret-key" not in str(err.value)


class Engine:
    def __init__(self, name, fail=False):
        self.name, self.fail, self.calls = name, fail, 0

    def synthesize(self, text, blend=None):
        self.calls += 1
        if self.fail:
            raise TTSError("down")
        return Speech(b"\x00\x00", "pcm16", 24000, 1 / 24000)


class FakeAzure(AzureTTS):
    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def synthesize(self, text, blend=None):
        self.calls += 1
        if self.fail:
            raise TTSError("down")
        return Speech(b"\x00\x00", "pcm16", 24000, 1 / 24000)


class FakeKokoroEngine(KokoroTTS):
    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def synthesize(self, text, blend=None):
        self.calls += 1
        if self.fail:
            raise TTSError("down")
        return Speech(b"\x00\x00", "pcm16", 24000, 1 / 24000)


BLEND = VoiceBlend.parse({"af_heart": 1})


async def test_kokoro_first_with_azure_as_the_fallback():
    kokoro, azure = FakeKokoroEngine(), FakeAzure()
    assert await TTS(kokoro, azure).synthesize("Hi.", BLEND, private=False) is not None
    assert (kokoro.calls, azure.calls) == (1, 0)
    kokoro, azure = FakeKokoroEngine(fail=True), FakeAzure()
    assert await TTS(kokoro, azure).synthesize("Hi.", BLEND, private=False) is not None
    assert (kokoro.calls, azure.calls) == (1, 1)


async def test_azure_first_when_preferred():
    kokoro, azure = FakeKokoroEngine(), FakeAzure()
    assert await TTS(kokoro, azure, prefer_azure=True).synthesize("Hi.", BLEND, private=False) is not None
    assert (kokoro.calls, azure.calls) == (0, 1)
    kokoro, azure = FakeKokoroEngine(), FakeAzure(fail=True)
    assert await TTS(kokoro, azure, prefer_azure=True).synthesize("Hi.", BLEND, private=False) is not None
    assert (kokoro.calls, azure.calls) == (1, 1)


async def test_private_mode_never_calls_azure_even_after_kokoro_fails():
    kokoro, azure = FakeKokoroEngine(fail=True), FakeAzure()
    assert await TTS(kokoro, azure).synthesize("Hi.", BLEND, private=True) is None
    assert (kokoro.calls, azure.calls) == (1, 0)
    kokoro, azure = FakeKokoroEngine(), FakeAzure()
    assert await TTS(kokoro, azure, prefer_azure=True).synthesize("Hi.", BLEND, private=True) is not None
    assert (kokoro.calls, azure.calls) == (1, 0)


async def test_nothing_can_speak_gives_none():
    assert await TTS(FakeKokoroEngine(fail=True), FakeAzure(fail=True)).synthesize("Hi.", BLEND, False) is None


def test_make_tts_follows_settings_credentials_and_private_mode(tmp_path):
    keys = KeyStore()
    keys.set_many({"azure_speech": "k"})
    kokoro = KokoroTTS(tmp_path)
    tts = make_tts(AppSettings.model_validate({"voice": {"azure_region": "uksouth", "tts": "azure"}}), keys, kokoro)
    assert tts.azure is not None and tts.prefer_azure
    tts = make_tts(AppSettings.model_validate({"voice": {"azure_region": "uksouth"}}), keys, kokoro)
    assert tts.azure is not None and not tts.prefer_azure
    assert make_tts(AppSettings(), keys, kokoro).azure is None  # no region
    assert make_tts(AppSettings.model_validate({"voice": {"azure_region": "uksouth"}}), KeyStore(), kokoro).azure is None
    private = AppSettings.model_validate({"private_mode": True, "voice": {"azure_region": "uksouth", "tts": "azure"}})
    assert make_tts(private, keys, kokoro).azure is None


@pytest.mark.skipif(not os.environ.get("AETHEL_VOICE_IT"), reason="needs the real models")
def test_real_kokoro_speaks_and_a_blend_differs():
    k = KokoroTTS()
    a = k.synthesize("Hello.", VoiceBlend.parse({"af_heart": 1}))
    b = k.synthesize("Hello.", VoiceBlend.parse({"af_heart": 0.5, "bf_emma": 0.5}))
    assert a.duration_s > 0.3 and a.sample_rate == 24000
    n = min(len(a.audio), len(b.audio)) // 2
    x, y = (np.frombuffer(s.audio, dtype=np.int16)[:n].astype(float) for s in (a, b))
    assert np.sqrt(np.mean((x - y) ** 2)) > 0
