"""
stt_engine.py

Speech-to-text (voice input) via faster-whisper.

Fully local: the Whisper model downloads once into models/stt/ and runs on CPU
with int8 quantization by default (STT_DEVICE/STT_COMPUTE_TYPE to change).
The frontend records audio (webm/wav) from the microphone and POSTs it to
/stt/transcribe; we hand the bytes to faster-whisper, which decodes via ffmpeg
bundled in the `av` package — no system ffmpeg needed.
"""

from __future__ import annotations

import io
import threading
from typing import Optional

from config import (
    MODELS_DIR,
    STT_ENABLED,
    STT_MODEL_SIZE,
    STT_DEVICE,
    STT_COMPUTE_TYPE,
)

_lock = threading.Lock()
_model = None
_load_error: Optional[str] = None


def _get_model():
    """Lazy-loads the faster-whisper model once. Returns None on failure."""
    global _model, _load_error
    if _model is not None:
        return _model
    if _load_error:
        return None
    with _lock:
        if _model is not None:
            return _model
        try:
            from faster_whisper import WhisperModel
            print(f"[STT] Loading faster-whisper '{STT_MODEL_SIZE}' ({STT_DEVICE}/{STT_COMPUTE_TYPE})...")
            _model = WhisperModel(
                STT_MODEL_SIZE,
                device=STT_DEVICE,
                compute_type=STT_COMPUTE_TYPE,
                download_root=str(MODELS_DIR / "stt"),
            )
            print("[STT] faster-whisper ready.")
            return _model
        except Exception as e:
            _load_error = f"faster-whisper unavailable: {e}"
            print(f"[STT] {_load_error}")
            return None


def transcribe_audio(audio_bytes: bytes, language: Optional[str] = None) -> dict:
    """
    Transcribes an audio payload (any ffmpeg-decodable container: webm, wav,
    mp3, m4a...). Returns {"text": str, "language": str, "duration": float}
    or {"error": str}.
    """
    if not STT_ENABLED:
        return {"error": "STT is disabled (STT_ENABLED=false)."}

    model = _get_model()
    if model is None:
        return {"error": _load_error or "STT model failed to load."}

    try:
        segments, info = model.transcribe(
            io.BytesIO(audio_bytes),
            language=language,
            beam_size=5,
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return {
            "text": text,
            "language": info.language,
            "duration": round(info.duration, 2),
        }
    except Exception as e:
        return {"error": f"Transcription failed: {e}"}


def warm_up() -> bool:
    """Triggers the model download/load ahead of the first mic use.
    Called from the server's background startup task. Returns True if ready."""
    return _get_model() is not None


def get_stt_status() -> dict:
    return {
        "enabled": STT_ENABLED,
        "model_size": STT_MODEL_SIZE,
        "device": STT_DEVICE,
        "loaded": _model is not None,
        "load_error": _load_error,
    }
