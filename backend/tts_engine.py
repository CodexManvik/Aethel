"""
tts_engine.py

Pluggable text-to-speech for AETHEL.

Engines:
  kokoro : Kokoro-82M via kokoro-onnx — fully local, CPU-friendly, near-SOTA
           quality. Needs two files in models/tts/:
             kokoro-v1.0.onnx   (model)
             voices-v1.0.bin    (voice pack)
  edge   : Edge-TTS (Microsoft cloud) — kept as a zero-setup fallback.

Selection: TTS_ENGINE env/config chooses the engine; if kokoro is selected but
its model files or the kokoro-onnx package are missing, we transparently fall
back to edge so voice never hard-fails. The active engine is reported via
get_tts_status() so the UI can show what is actually speaking.

Adding future engines (Chatterbox, Qwen3-TTS, ...) = implement _synth_<name>()
returning a saved audio filename and add it to _ENGINES.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Optional

from config import (
    MODELS_DIR,
    TEMP_IMAGE_DIR,
    TTS_ENGINE,
    TTS_KOKORO_VOICE,
    TTS_KOKORO_SPEED,
)

KOKORO_MODEL_PATH = MODELS_DIR / "tts" / "kokoro-v1.0.onnx"
KOKORO_VOICES_PATH = MODELS_DIR / "tts" / "voices-v1.0.bin"

# Official release assets of the kokoro-onnx project (Apache-2.0).
_KOKORO_DOWNLOADS = [
    (
        KOKORO_MODEL_PATH,
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
    ),
    (
        KOKORO_VOICES_PATH,
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
    ),
]

_lock = threading.Lock()
_download_lock = threading.Lock()
_kokoro = None
_kokoro_failed: Optional[str] = None
_download_state: str = "idle"  # idle | downloading | done | failed


def ensure_kokoro_models() -> bool:
    """
    Downloads the Kokoro model + voice pack into models/tts/ if missing.
    Streams to a .part file and renames on completion so a half-finished
    download is never mistaken for a valid model. Thread-safe; returns True
    when both files are present.
    """
    global _download_state
    if kokoro_available():
        _download_state = "done"
        return True

    with _download_lock:
        if kokoro_available():
            _download_state = "done"
            return True
        _download_state = "downloading"
        import requests

        for target, url in _KOKORO_DOWNLOADS:
            if target.exists():
                continue
            part = target.with_suffix(target.suffix + ".part")
            try:
                print(f"[TTS] Downloading {target.name} (one-time setup)...")
                with requests.get(url, stream=True, timeout=30, allow_redirects=True) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0))
                    done = 0
                    next_report = 10
                    with open(part, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)
                                done += len(chunk)
                                if total and (done * 100 // total) >= next_report:
                                    print(f"[TTS]   {target.name}: {done * 100 // total}%")
                                    next_report += 10
                part.replace(target)
                print(f"[TTS] {target.name} downloaded ({done // (1024 * 1024)} MB).")
            except Exception as e:
                print(f"[TTS] Download of {target.name} failed: {e}")
                try:
                    part.unlink(missing_ok=True)
                except Exception:
                    pass
                _download_state = "failed"
                return False

        _download_state = "done" if kokoro_available() else "failed"
        return _download_state == "done"


# ─────────────────────────────────────────────
# Text preparation
# ─────────────────────────────────────────────

def clean_text_for_speech(text: str) -> str:
    """Strips roleplay actions, markdown, tags, and emoji before synthesis."""
    spoken = re.sub(r"\*.*?\*", "", text)                      # *actions*
    spoken = re.sub(r"\[.*?\]", "", spoken)                    # [tags]
    spoken = re.sub(r"```.*?```", "", spoken, flags=re.DOTALL)  # code blocks
    spoken = re.sub(r"[#>`_~]", "", spoken)                    # md leftovers
    spoken = re.sub(r"[☀-➿]|[\U00010000-\U0010FFFF]", "", spoken)
    spoken = re.sub(r"\s+", " ", spoken).strip()
    return spoken


# ─────────────────────────────────────────────
# Kokoro (local)
# ─────────────────────────────────────────────

def kokoro_available() -> bool:
    return KOKORO_MODEL_PATH.exists() and KOKORO_VOICES_PATH.exists()


def _get_kokoro():
    """Lazy-loads the Kokoro ONNX pipeline once. Returns None on failure."""
    global _kokoro, _kokoro_failed
    if _kokoro is not None:
        return _kokoro
    if _kokoro_failed:
        return None
    with _lock:
        if _kokoro is not None:
            return _kokoro
        if not kokoro_available():
            _kokoro_failed = "model files missing in models/tts/"
            return None
        try:
            from kokoro_onnx import Kokoro
            _kokoro = Kokoro(str(KOKORO_MODEL_PATH), str(KOKORO_VOICES_PATH))
            print("[TTS] Kokoro local engine loaded.")
            return _kokoro
        except Exception as e:
            _kokoro_failed = f"kokoro-onnx load failed: {e}"
            print(f"[TTS] {_kokoro_failed} — falling back to edge-tts.")
            return None


def _synth_kokoro(text: str, voice_override: Optional[str] = None) -> Optional[str]:
    kokoro = _get_kokoro()
    if kokoro is None:
        return None
    try:
        import soundfile as sf
        voice = voice_override or TTS_KOKORO_VOICE
        samples, sample_rate = kokoro.create(text, voice=voice, speed=TTS_KOKORO_SPEED)
        filename = f"voice_{os.urandom(6).hex()}.wav"
        filepath = os.path.join(TEMP_IMAGE_DIR, filename)
        sf.write(filepath, samples, sample_rate)
        return filename
    except Exception as e:
        print(f"[TTS] Kokoro synthesis error: {e}")
        return None


# ─────────────────────────────────────────────
# Edge-TTS (cloud fallback)
# ─────────────────────────────────────────────

async def _synth_edge(text: str, settings: dict) -> Optional[str]:
    try:
        import edge_tts
        voice = settings.get("edge_tts_voice", "en-GB-SoniaNeural")
        rate = settings.get("edge_tts_rate", "-5%")
        pitch = settings.get("edge_tts_pitch", "-5Hz")
        filename = f"voice_{os.urandom(6).hex()}.mp3"
        filepath = os.path.join(TEMP_IMAGE_DIR, filename)
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        await communicate.save(filepath)
        return filename
    except Exception as e:
        print(f"[TTS] edge-tts synthesis error: {e}")
        return None


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def _load_voice_settings() -> dict:
    import json
    settings_path = Path(__file__).parent / "data" / "settings.json"
    if settings_path.exists():
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


async def synthesize(text: str) -> Optional[str]:
    """
    Synthesizes speech for the given text.
    Returns the audio filename inside TEMP_IMAGE_DIR (served at /images/),
    or None if no engine could produce audio.
    """
    spoken = clean_text_for_speech(text)
    if not spoken:
        return None

    settings = _load_voice_settings()
    engine = str(settings.get("tts_engine", TTS_ENGINE)).lower()

    if engine == "kokoro":
        import asyncio
        voice = settings.get("kokoro_voice")
        filename = await asyncio.to_thread(_synth_kokoro, spoken, voice)
        if filename:
            return filename
        # transparent fallback
        return await _synth_edge(spoken, settings)

    return await _synth_edge(spoken, settings)


def get_tts_status() -> dict:
    """Reports which engine is configured and what is actually available."""
    settings = _load_voice_settings()
    engine = str(settings.get("tts_engine", TTS_ENGINE)).lower()
    return {
        "configured_engine": engine,
        "kokoro_model_present": kokoro_available(),
        "kokoro_download_state": _download_state,
        "kokoro_load_error": _kokoro_failed,
        "active_engine": (
            "kokoro" if engine == "kokoro" and kokoro_available() and not _kokoro_failed
            else "edge"
        ),
    }
