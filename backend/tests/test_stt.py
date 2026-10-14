import struct
import wave
import io

import httpx
import numpy as np
import pytest

from aethel.keys import KeyStore
from aethel.settings import AppSettings
from aethel.voice import stt as stt_module
from aethel.voice.azure import AzureSpeech, azure_from, valid_region
from aethel.voice.stt import (AzureSTT, FallbackSTT, LocalWhisper, SpeechToTextError, Transcript, make_stt,
                              pcm16_to_wav)

AZURE = AzureSpeech(key="secret-key", region="uksouth")


def pcm(seconds=1.0, value=1000):
    return struct.pack("<h", value) * int(16000 * seconds)


def azure_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def settings(**voice):
    return AppSettings.model_validate({"voice": voice})


def test_wav_header_is_16k_mono_16bit_with_the_data_length():
    wav = pcm16_to_wav(pcm(0.5))
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE" and len(wav) == 44 + 16000
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 16000, 8000)


def test_azure_request_shape_and_text_parsed():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = request.content
        return httpx.Response(200, json={"RecognitionStatus": "Success", "DisplayText": "Hello there.",
                                         "Offset": 0, "Duration": 1})

    result = AzureSTT(AZURE, "en-GB", azure_client(handler)).transcribe(pcm(1.0))
    assert isinstance(result, Transcript)
    assert (result.text, result.language, result.duration_s) == ("Hello there.", "en-GB", 1.0)
    assert seen["url"].startswith("https://uksouth.stt.speech.microsoft.com/speech/recognition/conversation/"
                                  "cognitiveservices/v1?")
    assert "language=en-GB" in seen["url"] and "format=simple" in seen["url"]
    assert seen["headers"]["ocp-apim-subscription-key"] == "secret-key"
    assert seen["headers"]["content-type"] == "audio/wav; codecs=audio/pcm; samplerate=16000"
    assert seen["body"][:4] == b"RIFF" and len(seen["body"]) == 44 + 32000


@pytest.mark.parametrize("status", ["NoMatch", "InitialSilenceTimeout", "BabbleTimeout"])
def test_azure_hearing_nothing_is_empty_text_not_an_error(status):
    client = azure_client(lambda r: httpx.Response(200, json={"RecognitionStatus": status}))
    assert AzureSTT(AZURE, "en-GB", client).transcribe(pcm()).text == ""


@pytest.mark.parametrize("code,fragment", [(401, "refused the key"), (403, "refused the key"), (429, "rate-limiting"),
                                           (500, "answered 500")])
def test_azure_errors_have_plain_messages_and_never_the_key(code, fragment):
    client = azure_client(lambda r: httpx.Response(code, text="secret-key leaked here"))
    with pytest.raises(SpeechToTextError) as err:
        AzureSTT(AZURE, "en-GB", client).transcribe(pcm())
    assert fragment in str(err.value) and "secret-key" not in str(err.value)


def test_azure_unreachable_and_unreadable():
    def down(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(SpeechToTextError, match="couldn't reach Azure"):
        AzureSTT(AZURE, "en-GB", azure_client(down)).transcribe(pcm())
    with pytest.raises(SpeechToTextError, match="wasn't readable"):
        AzureSTT(AZURE, "en-GB", azure_client(lambda r: httpx.Response(200, text="<html>"))).transcribe(pcm())
    with pytest.raises(SpeechToTextError, match="reported Error"):
        AzureSTT(AZURE, "en-GB", azure_client(lambda r: httpx.Response(200, json={"RecognitionStatus": "Error"}))
                 ).transcribe(pcm())


def test_azure_audio_over_a_minute_is_refused_without_a_request():
    def never(request):
        raise AssertionError("no request expected")

    with pytest.raises(SpeechToTextError, match="too long"):
        AzureSTT(AZURE, "en-GB", azure_client(never)).transcribe(pcm(61))


def test_region_is_checked_before_it_becomes_part_of_a_host():
    assert valid_region("uksouth") and valid_region("eastus2")
    for bad in ("", "evil.example/x", "a b", "UKSOUTH", "x", "uk-south", "a" * 40, "uksouth@evil.com"):
        assert not valid_region(bad)
    keys = KeyStore()
    keys.set_many({"azure_speech": "k"})
    assert azure_from(settings(), keys) is None  # no region
    assert azure_from(settings(azure_region="uksouth"), keys) == AzureSpeech("k", "uksouth")
    assert azure_from(settings(azure_region="uksouth"), KeyStore()) is None  # no key


def test_region_setting_rejects_a_host():
    with pytest.raises(ValueError):
        settings(azure_region="evil.example/x")


def test_azure_region_can_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_REGION", "WestEurope")
    keys = KeyStore()
    keys.set_many({"azure_speech": "k"})
    assert azure_from(settings(), keys) == AzureSpeech("k", "westeurope")


def test_credentials_never_appear_in_repr():
    assert "secret-key" not in repr(AZURE) and "secret-key" not in str(AZURE)


class FakeWhisperModel:
    def __init__(self, *args, **kwargs):
        FakeWhisperModel.init = (args, kwargs)

    def transcribe(self, audio, **kwargs):
        FakeWhisperModel.audio, FakeWhisperModel.kwargs = audio, kwargs
        seg = lambda t: type("S", (), {"text": t})()  # noqa: E731
        return iter([seg(" Hello"), seg(" world. ")]), type("I", (), {"language": "en"})()


def test_local_whisper_converts_to_float_range_and_joins_segments(monkeypatch):
    import faster_whisper
    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeWhisperModel)
    samples = np.array([0, 16384, -32768, 32767], dtype=np.int16).tobytes()
    result = LocalWhisper(size="small").transcribe(samples)
    assert result.text == "Hello world." and result.language == "en"
    assert FakeWhisperModel.audio.dtype == np.float32
    assert list(FakeWhisperModel.audio) == [0.0, 0.5, -1.0, 32767 / 32768]
    assert FakeWhisperModel.kwargs["beam_size"] == 1 and FakeWhisperModel.kwargs["vad_filter"] is False
    assert FakeWhisperModel.init[0][0] == "small" and FakeWhisperModel.init[1]["compute_type"] == "int8"


def test_local_whisper_failure_is_a_plain_error(monkeypatch):
    import faster_whisper

    class Broken:
        def __init__(self, *a, **k):
            raise RuntimeError("no model")

    monkeypatch.setattr(faster_whisper, "WhisperModel", Broken)
    with pytest.raises(SpeechToTextError, match="isn't available"):
        LocalWhisper().transcribe(pcm())


def keys_with_azure():
    keys = KeyStore()
    keys.set_many({"azure_speech": "k"})
    return keys


def test_make_stt_choices():
    azure = settings(azure_region="uksouth")
    assert isinstance(make_stt(azure, keys_with_azure()), FallbackSTT)
    assert make_stt(azure, keys_with_azure()).name == "azure:uksouth"
    assert isinstance(make_stt(settings(azure_region="uksouth", stt="local"), keys_with_azure()), LocalWhisper)
    assert isinstance(make_stt(azure, KeyStore()), LocalWhisper)  # no key
    assert isinstance(make_stt(settings(), keys_with_azure()), LocalWhisper)  # no region
    private = AppSettings.model_validate({"private_mode": True, "voice": {"azure_region": "uksouth"}})
    assert isinstance(make_stt(private, keys_with_azure()), LocalWhisper)  # nothing leaves the PC


def test_fallback_hears_locally_when_azure_fails():
    class Failing:
        name = "azure:uksouth"

        def transcribe(self, pcm16, sample_rate=16000):
            raise SpeechToTextError("down")

    class Local:
        name = "local"

        def transcribe(self, pcm16, sample_rate=16000):
            return Transcript("heard locally", "en", 1.0, 5)

    assert FallbackSTT(Failing(), Local()).transcribe(pcm()).text == "heard locally"


@pytest.mark.skipif(not __import__("os").environ.get("AETHEL_VOICE_IT"), reason="needs the real models")
def test_real_local_model_hears_kokoro_say_hello():
    from aethel.voice.tts import KokoroTTS, VoiceBlend
    speech = KokoroTTS().synthesize("Hello there, how are you today?", VoiceBlend((("af_heart", 1.0),)))
    audio = np.frombuffer(speech.audio, dtype=np.int16).astype(np.float32)
    ratio = 16000 / speech.sample_rate  # a plain resample is enough for a smoke test
    idx = (np.arange(int(len(audio) * ratio)) / ratio).astype(int)
    result = LocalWhisper().transcribe(audio[idx].astype(np.int16).tobytes())
    assert "hello" in result.text.lower()
