"""Text to speech. Kokoro runs on this PC and speaks in a persona's voice blend; Azure Speech speaks with a neural
voice (the user's choice in Settings -> Voice, and the fallback when Kokoro fails). In private mode only Kokoro is
ever used: Azure is a cloud service.

Both return 16-bit mono PCM, which the call view plays without decoding."""
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Collection, Literal
from xml.sax.saxutils import escape

import anyio
import httpx
import numpy as np

from ..paths import MODELS_DIR
from .azure import AzureSpeech

log = logging.getLogger("aethel.voice")
VOICE_NAME = re.compile(r"^[a-z]{2}_[a-z0-9]+$")
KOKORO_LANG = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "j": "ja", "p": "pt-br",
               "z": "cmn"}
SPEED_MIN, SPEED_MAX = 0.5, 2.0
AZURE_TIMEOUT_S = 15.0


class TTSError(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceBlend:
    weights: tuple[tuple[str, float], ...]  # e.g. (("af_heart", 0.7), ("bf_emma", 0.3)); sums to 1
    speed: float = 1.0

    @staticmethod
    def parse(d: dict, known: Collection[str] | None = None) -> "VoiceBlend":
        """{"af_heart": 0.7, "bf_emma": 0.3, "speed": 1.0}. Weights are normalised; unknown voices (when the list of
        known ones is given), negative weights and an empty blend are rejected; the speed is clamped to 0.5–2.0."""
        raw = dict(d)
        speed = float(raw.pop("speed", 1.0))
        weights = []
        for name, weight in raw.items():
            if not VOICE_NAME.fullmatch(str(name)) or (known is not None and name not in known):
                raise ValueError(f"unknown voice: {name}")
            weight = float(weight)
            if weight < 0 or weight != weight:
                raise ValueError(f"a voice weight can't be negative: {name}")
            if weight > 0:
                weights.append((str(name), weight))
        total = sum(w for _, w in weights)
        if total <= 0:
            raise ValueError("a voice blend needs at least one voice")
        weights.sort(key=lambda nw: -nw[1])  # the main voice first: it decides the language
        return VoiceBlend(tuple((n, w / total) for n, w in weights), min(SPEED_MAX, max(SPEED_MIN, speed)))


@dataclass
class Speech:
    audio: bytes  # 16-bit mono PCM
    codec: Literal["pcm16"]
    sample_rate: int
    duration_s: float


def speakable(text: str) -> str:
    """What to say for a piece of chat text: no markdown marks, links as their words, no [3] citations, code and
    emoji left out."""
    spoken = re.sub(r"```.*?```", " (code) ", text, flags=re.DOTALL)
    spoken = re.sub(r"`([^`]*)`", r"\1", spoken)
    spoken = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", spoken)           # [words](url) -> words
    spoken = re.sub(r"\[\d+(?:\s*[,–-]\s*\d+)*\]", "", spoken)          # [3], [1, 2]
    spoken = re.sub(r"https?://\S+|www\.\S+", "a link", spoken)
    spoken = re.sub(r"[☀-➿]|[\U00010000-\U0010FFFF]|[‍️]", "", spoken)  # emoji
    spoken = re.sub(r"^\s{0,3}(?:[#>]+|[-*+]|\d+[.)])\s+", "", spoken, flags=re.MULTILINE)  # headings, quotes, bullets
    spoken = re.sub(r"[*_~#>`|]", "", spoken)
    return re.sub(r"\s+", " ", spoken).strip()


def _pcm16(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


class KokoroTTS:
    """kokoro-onnx, loaded on first use. `create` takes a voice name or a style array, so a blend is the weighted sum
    of the voices' style arrays."""

    def __init__(self, model_dir: Path | None = None):
        self.model_dir = model_dir or MODELS_DIR / "tts"
        self._kokoro = None
        self._lock = threading.Lock()
        self._styles: dict[VoiceBlend, np.ndarray] = {}

    @property
    def available(self) -> bool:
        return (self.model_dir / "kokoro-v1.0.onnx").is_file() and (self.model_dir / "voices-v1.0.bin").is_file()

    def warm(self) -> None:
        with self._lock:
            if self._kokoro is None:
                if not self.available:
                    raise TTSError("Kokoro's model files are missing from models/tts/.")
                from kokoro_onnx import Kokoro
                self._kokoro = Kokoro(str(self.model_dir / "kokoro-v1.0.onnx"), str(self.model_dir / "voices-v1.0.bin"))

    def voices(self) -> list[str]:
        self.warm()
        return sorted(self._kokoro.get_voices())

    def style(self, blend: VoiceBlend) -> np.ndarray:
        cached = self._styles.get(blend)
        if cached is None:
            self.warm()
            cached = sum(w * self._kokoro.get_voice_style(name) for name, w in blend.weights).astype(np.float32)
            self._styles[blend] = cached
        return cached

    def synthesize(self, text: str, blend: VoiceBlend) -> Speech:
        try:
            self.warm()
            lang = KOKORO_LANG.get(blend.weights[0][0][0], "en-us")
            samples, rate = self._kokoro.create(text, voice=self.style(blend), speed=blend.speed, lang=lang)
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError(f"Kokoro failed: {exc}") from exc
        return Speech(_pcm16(samples), "pcm16", int(rate), len(samples) / rate)


class AzureTTS:
    """Azure Speech's text-to-speech REST API, asking for raw 24 kHz PCM."""

    RATE = 24000

    def __init__(self, azure: AzureSpeech, voice: str, http: httpx.Client | None = None):
        self.azure, self.voice = azure, voice
        self._http = http or httpx.Client(trust_env=False, timeout=AZURE_TIMEOUT_S)

    def ssml(self, text: str, speed: float) -> str:
        locale = "-".join(self.voice.split("-")[:2])
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        return (f"<speak version='1.0' xml:lang='{locale}' xmlns='http://www.w3.org/2001/10/synthesis'>"
                f"<voice name='{self.voice}'><prosody rate='{round((speed - 1) * 100):+d}%'>{escape(text)}</prosody>"
                f"</voice></speak>")

    def synthesize(self, text: str, blend: VoiceBlend | None = None) -> Speech:
        try:
            resp = self._http.post(
                self.azure.tts_url, content=self.ssml(text, blend.speed if blend else 1.0).encode("utf-8"),
                headers={"Ocp-Apim-Subscription-Key": self.azure.key, "Content-Type": "application/ssml+xml",
                         "X-Microsoft-OutputFormat": "raw-24khz-16bit-mono-pcm", "User-Agent": "Aethel"},
                timeout=AZURE_TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise TTSError(f"couldn't reach Azure Speech ({type(exc).__name__})") from exc
        if resp.status_code in (401, 403):
            raise TTSError("Azure Speech refused the key or region")
        if resp.status_code != 200:
            raise TTSError(f"Azure Speech answered {resp.status_code}")
        audio = resp.content[: len(resp.content) // 2 * 2]
        return Speech(audio, "pcm16", self.RATE, len(audio) / 2 / self.RATE)


class TTS:
    """Speaks a sentence: the preferred engine first, the other as a fallback unless the conversation is private.
    Returns None when nothing could speak (logged), so a call goes on silently rather than dying."""

    def __init__(self, kokoro: KokoroTTS, azure: AzureTTS | None = None, prefer_azure: bool = False):
        self.kokoro, self.azure, self.prefer_azure = kokoro, azure, prefer_azure
        self.last_ms: int | None = None

    async def synthesize(self, text: str, blend: VoiceBlend, private: bool) -> Speech | None:
        order = [self.azure, self.kokoro] if self.prefer_azure else [self.kokoro, self.azure]
        t0 = time.perf_counter()
        for engine in order:
            if engine is None or (private and isinstance(engine, AzureTTS)):
                continue
            try:
                # A thread can't be stopped: if the call is cancelled (the user spoke over the reply), the
                # sentence in progress is finished in the background and its audio dropped.
                speech = await anyio.to_thread.run_sync(engine.synthesize, text, blend, abandon_on_cancel=True)
                self.last_ms = int((time.perf_counter() - t0) * 1000)
                return speech
            except Exception as exc:
                log.warning("%s couldn't speak: %s", type(engine).__name__, exc)
        return None


def make_tts(settings, keys, kokoro: KokoroTTS, http: httpx.Client | None = None) -> TTS:
    from .azure import azure_from
    s = settings.get() if hasattr(settings, "get") else settings
    azure_creds = None if s.private_mode else azure_from(s, keys)
    azure = AzureTTS(azure_creds, s.voice.azure_voice, http) if azure_creds else None
    return TTS(kokoro, azure, prefer_azure=azure is not None and s.voice.tts == "azure")
