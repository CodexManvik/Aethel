"""Speech to text. Azure Speech by default (it needs the user's key and region), the local faster-whisper model
otherwise: in private mode, without credentials, or when Azure can't be reached.

Both are blocking, so callers run them in a thread. An utterance is one VAD-cut stretch of 16 kHz mono PCM16 from
the call view, never longer than Azure's short-audio limit of 60 seconds."""
import io
import logging
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
import numpy as np

from ..paths import MODELS_DIR
from .azure import AzureSpeech, azure_from

log = logging.getLogger("aethel.voice")
MAX_UTTERANCE_S = 60.0       # Azure's short-audio REST limit
AZURE_TIMEOUT_S = 15.0
SAMPLE_RATE = 16000


class SpeechToTextError(RuntimeError):
    """Carries a message fit to show the user."""


@dataclass
class Transcript:
    text: str
    language: str | None
    duration_s: float
    latency_ms: int


class SpeechToText(Protocol):
    name: str

    def transcribe(self, pcm16: bytes, sample_rate: int = SAMPLE_RATE) -> Transcript: ...


def pcm16_to_wav(pcm16: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """A mono 16-bit WAV: the 44-byte RIFF header and the samples."""
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16)
    return out.getvalue()


def _duration(pcm16: bytes, sample_rate: int) -> float:
    return len(pcm16) / 2 / sample_rate


class LocalWhisper:
    """faster-whisper on this PC. Beam size 1 and no internal VAD: the call view already cut the utterance, and in
    conversation speed matters more than the last fraction of accuracy."""

    def __init__(self, size: str = "small", device: str = "cpu", compute_type: str = "int8",
                 model_dir: Path | None = None):
        self.size, self.device, self.compute_type = size, device, compute_type
        self.model_dir = model_dir or MODELS_DIR / "stt"
        self.name = f"local:faster-whisper-{size}"
        self._model = None

    def warm(self) -> None:
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.size, device=self.device, compute_type=self.compute_type,
                                       download_root=str(self.model_dir))

    def transcribe(self, pcm16: bytes, sample_rate: int = SAMPLE_RATE) -> Transcript:
        t0 = time.perf_counter()
        try:
            self.warm()
            audio = np.frombuffer(pcm16[: len(pcm16) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
            segments, info = self._model.transcribe(audio, beam_size=1, vad_filter=False, language=None,
                                                    condition_on_previous_text=False)
            text = " ".join(s.text.strip() for s in segments).strip()
        except Exception as exc:
            log.warning("local speech recognition failed: %r", exc)
            raise SpeechToTextError(f"Speech recognition isn't available: {exc}") from exc
        return Transcript(text=text, language=getattr(info, "language", None), duration_s=_duration(pcm16, sample_rate),
                          latency_ms=int((time.perf_counter() - t0) * 1000))


class AzureSTT:
    """Azure Speech, the short-audio REST API: one POST per utterance. It takes a locale, not auto-detection, so the
    language is a setting."""

    def __init__(self, azure: AzureSpeech, language: str, http: httpx.Client | None = None):
        self.azure, self.language = azure, language
        self.name = f"azure:{azure.region}"
        self._http = http or httpx.Client(trust_env=False, timeout=AZURE_TIMEOUT_S)

    def transcribe(self, pcm16: bytes, sample_rate: int = SAMPLE_RATE) -> Transcript:
        t0 = time.perf_counter()
        duration = _duration(pcm16, sample_rate)
        if duration > MAX_UTTERANCE_S:
            raise SpeechToTextError("That was too long for speech recognition (over a minute).")
        try:
            resp = self._http.post(
                self.azure.stt_url, params={"language": self.language, "format": "simple"},
                headers={"Ocp-Apim-Subscription-Key": self.azure.key, "Accept": "application/json",
                         "Content-Type": f"audio/wav; codecs=audio/pcm; samplerate={sample_rate}"},
                content=pcm16_to_wav(pcm16, sample_rate), timeout=AZURE_TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise SpeechToTextError(f"Speech recognition isn't available: couldn't reach Azure Speech "
                                    f"({type(exc).__name__}).") from exc
        if resp.status_code in (401, 403):
            raise SpeechToTextError("Speech recognition isn't available: Azure Speech refused the key or region. "
                                    "Check them in Settings → Voice.")
        if resp.status_code == 429:
            raise SpeechToTextError("Speech recognition is busy: Azure Speech is rate-limiting this key.")
        if resp.status_code != 200:
            raise SpeechToTextError(f"Speech recognition isn't available: Azure Speech answered {resp.status_code}.")
        try:
            body = resp.json()
        except ValueError as exc:
            raise SpeechToTextError("Speech recognition isn't available: Azure Speech's answer wasn't readable.") from exc
        status = body.get("RecognitionStatus")
        if status == "Success":
            text = str(body.get("DisplayText") or "").strip()
        elif status in ("NoMatch", "InitialSilenceTimeout", "BabbleTimeout"):
            text = ""  # nothing intelligible: not an error, there's just nothing to answer
        else:
            raise SpeechToTextError(f"Speech recognition isn't available: Azure Speech reported {status or 'an error'}.")
        return Transcript(text=text, language=self.language, duration_s=duration,
                          latency_ms=int((time.perf_counter() - t0) * 1000))


class FallbackSTT:
    """Azure first; if it fails (offline, a bad key, a rate limit), this utterance is heard locally instead."""

    def __init__(self, primary: SpeechToText, fallback: SpeechToText):
        self.primary, self.fallback = primary, fallback
        self.name = primary.name

    def warm(self) -> None:
        if hasattr(self.primary, "warm"):
            self.primary.warm()

    def transcribe(self, pcm16: bytes, sample_rate: int = SAMPLE_RATE) -> Transcript:
        try:
            return self.primary.transcribe(pcm16, sample_rate)
        except SpeechToTextError as exc:
            log.warning("%s failed (%s); using %s", self.primary.name, exc, self.fallback.name)
            return self.fallback.transcribe(pcm16, sample_rate)


def make_stt(settings, keys, http: httpx.Client | None = None, local: LocalWhisper | None = None) -> SpeechToText:
    """Private mode and "local" are always local. Otherwise Azure when credentials exist, with the local model behind
    it; without credentials, local alone. (`local` lets a caller share one loaded model between calls.)"""
    s = settings.get() if hasattr(settings, "get") else settings
    local = local or LocalWhisper(size=s.voice.stt_model)
    if s.private_mode or s.voice.stt == "local":
        return local
    azure = azure_from(s, keys)
    if azure is None:
        return local
    return FallbackSTT(AzureSTT(azure, s.voice.language, http), local)
