"""
emotion_engine.py

Optional conversational-tone tracking.

Gives the assistant continuity of tone across a conversation, so a long chat
doesn't read as a sequence of unrelated stateless replies. This is a *feature*,
not a core capability — it is disabled by default (EMOTION_ENGINE_ENABLED) and
the rest of the system works identically without it.

Model
-----
Two dimensions, following the circumplex model of affect (Russell, 1980), with
plain-English names:

    mood    — negative ←→ positive   (how pleasant the conversation feels)
    energy  — low      ←→ high       (how activated/intense it feels)

Each classified emotion maps to a point on that plane; the conversation's
running state drifts toward those points and decays back toward baseline.

Classification backends (tried in order, first available wins):
    1. ONNX  — a local distilroberta emotion classifier, if EMOTION_MODEL_DIR
               contains model.onnx. Most accurate.
    2. lexicon — a small built-in word list. Zero dependencies, no download,
               works out of the box. Coarse but adequate for tone drift.

Everything is lazy: nothing is loaded until the first classification, and
nothing at all is loaded when the engine is disabled.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Optional, Tuple

from config import (
    EMOTION_ENGINE_ENABLED,
    BASELINE_MOOD,
    BASELINE_ENERGY,
    EMOTION_MODEL_DIR,
    EMOTION_TOKENIZER_NAME,
    EMOTION_CPU_ONLY,
    EMOTION_DELTA_MULTIPLIER,
)

# ─────────────────────────────────────────────────────────────
# Emotion → (mood, energy) coordinates
#
# Placements follow the standard circumplex layout: joy is pleasant and
# moderately activated; anger is unpleasant and highly activated; sadness is
# unpleasant and deactivated, etc. Values are directional targets, not
# measurements — the delta multiplier controls how far each turn nudges state.
# ─────────────────────────────────────────────────────────────

# Below this on both axes the conversation counts as neutral rather than
# faintly positive/negative — keeps the UI at a calm resting state.
NEUTRAL_BAND = 0.18

EMOTION_LABELS = ["anger", "disgust", "fear", "joy", "neutral", "sadness", "surprise"]

EMOTION_COORDS = {
    "joy":      {"mood":  0.8, "energy":  0.4},
    "surprise": {"mood":  0.6, "energy":  0.6},
    "anger":    {"mood": -0.8, "energy":  0.8},
    "fear":     {"mood": -0.5, "energy":  0.6},
    "disgust":  {"mood": -0.6, "energy":  0.2},
    "sadness":  {"mood": -0.7, "energy": -0.4},
    "neutral":  {"mood":  0.0, "energy":  0.0},
}

# Lexicon fallback — used when no ONNX model is present so the feature works
# without an extra download. Deliberately small; it only needs to detect the
# broad direction of a message.
_POSITIVE = {
    "thanks", "thank", "great", "awesome", "love", "nice", "good", "happy",
    "perfect", "excellent", "amazing", "glad", "appreciate", "cool", "yes",
    "please", "wonderful", "brilliant", "helpful", "works", "worked", "fixed",
}
_NEGATIVE = {
    "hate", "awful", "terrible", "bad", "wrong", "broken", "annoying", "stupid",
    "useless", "fail", "failed", "error", "bug", "angry", "sad", "sorry",
    "confused", "frustrating", "worse", "worst", "no", "not", "cant", "won't",
}
_HIGH_ENERGY = {
    "now", "urgent", "quickly", "asap", "immediately", "wow", "amazing", "hate",
    "angry", "excited", "please", "hurry", "fast", "come", "on",
}
_LOW_ENERGY = {
    "tired", "sleepy", "later", "whenever", "maybe", "boring", "bored",
    "slow", "calm", "quiet", "relax", "meh", "fine",
}

_lock = threading.Lock()
_session = None
_tokenizer = None
_backend: Optional[str] = None   # "onnx" | "lexicon" | None


# ─────────────────────────────────────────────────────────────
# Backend loading (lazy)
# ─────────────────────────────────────────────────────────────

def _ensure_backend() -> Optional[str]:
    """Loads a classification backend on first use. Returns its name, or None."""
    global _session, _tokenizer, _backend
    if _backend is not None:
        return _backend
    with _lock:
        if _backend is not None:
            return _backend

        model_file = os.path.join(EMOTION_MODEL_DIR, "model.onnx")
        if os.path.isfile(model_file):
            try:
                import numpy as np  # noqa: F401  (used by the classifier path)
                import onnxruntime as ort
                from transformers import AutoTokenizer

                providers = (
                    ["CPUExecutionProvider"] if EMOTION_CPU_ONLY
                    else ["CUDAExecutionProvider", "CPUExecutionProvider"]
                )
                _tokenizer = AutoTokenizer.from_pretrained(EMOTION_TOKENIZER_NAME)
                _session = ort.InferenceSession(model_file, providers=providers)
                _backend = "onnx"
                print("[TONE] ONNX emotion classifier loaded.")
                return _backend
            except Exception as e:
                print(f"[TONE] ONNX classifier unavailable ({e}); using lexicon fallback.")

        _backend = "lexicon"
        return _backend


def _classify_onnx(text: str) -> Optional[str]:
    """Returns the dominant emotion label via the ONNX classifier."""
    import numpy as np

    inputs = _tokenizer(text, return_tensors="np", truncation=True, max_length=128)
    onnx_inputs = {
        "input_ids": inputs["input_ids"].astype(np.int64),
        "attention_mask": inputs["attention_mask"].astype(np.int64),
    }
    if "token_type_ids" in inputs:
        onnx_inputs["token_type_ids"] = inputs["token_type_ids"].astype(np.int64)

    logits = _session.run(None, onnx_inputs)[0][0]
    exp = np.exp(logits - np.max(logits))
    probs = exp / exp.sum()
    return EMOTION_LABELS[int(probs.argmax())]


def _classify_lexicon(text: str) -> Tuple[float, float]:
    """Coarse (mood, energy) estimate from word lists. Returns raw coordinates."""
    words = set(re.findall(r"[a-z']+", text.lower()))
    pos, neg = len(words & _POSITIVE), len(words & _NEGATIVE)
    hi, lo = len(words & _HIGH_ENERGY), len(words & _LOW_ENERGY)

    total = pos + neg
    mood = 0.0 if total == 0 else (pos - neg) / total
    e_total = hi + lo
    energy = 0.0 if e_total == 0 else (hi - lo) / e_total

    # Exclamation marks and shouting read as activation.
    if "!" in text:
        energy = min(1.0, energy + 0.3)
    return mood, energy


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

def calculate_text_delta(text: str) -> Tuple[float, float]:
    """
    Returns (mood_shift, energy_shift) for a message — how far this turn should
    nudge the conversation's tone. (0.0, 0.0) when disabled or on any failure,
    so callers never need to special-case it.
    """
    if not EMOTION_ENGINE_ENABLED or not text.strip():
        return 0.0, 0.0

    try:
        backend = _ensure_backend()
        if backend == "onnx":
            label = _classify_onnx(text)
            coords = EMOTION_COORDS.get(label, EMOTION_COORDS["neutral"])
            mood, energy = coords["mood"], coords["energy"]
        else:
            mood, energy = _classify_lexicon(text)

        return mood * EMOTION_DELTA_MULTIPLIER, energy * EMOTION_DELTA_MULTIPLIER
    except Exception as e:
        print(f"[TONE] Classification failed ({e}); no tone shift applied.")
        return 0.0, 0.0


def get_affective_state(mood: float, energy: float) -> Tuple[str, str, str]:
    """
    Maps (mood, energy) to (label, tone, depth) for prompt injection and the UI.

    Labels describe the *conversation's* character, not a claim about machine
    feelings — they steer wording, not roleplay.
    """
    if not EMOTION_ENGINE_ENABLED:
        return "Neutral", "casual", "steady"

    # Deadzone: near-baseline readings are genuinely neutral. Without this,
    # a mood of 0.01 would report "Positive" and the UI would never show a
    # calm resting state.
    if abs(mood) < NEUTRAL_BAND and abs(energy) < NEUTRAL_BAND:
        return "Neutral", "casual", "steady"

    if mood >= 0:
        if energy > 0.5:
            return "Enthusiastic", "lively", "high"
        if mood > 0.5:
            return "Warm", "warm", "deep"
        if energy < -0.5:
            return "Relaxed", "soft", "surface"
        if energy < 0:
            return "Calm", "relaxed", "steady"
        return "Positive", "engaged", "steady"

    if energy > 0.5:
        return "Tense", "direct", "deep"
    if mood < -0.5:
        return "Downcast", "gentle", "deep"
    if energy < 0:
        return "Flat", "measured", "surface"
    return "Guarded", "careful", "surface"


def get_tone_status() -> dict:
    """Reports engine availability for the settings UI."""
    return {
        "enabled": EMOTION_ENGINE_ENABLED,
        "backend": _backend if _backend else ("not loaded" if EMOTION_ENGINE_ENABLED else "disabled"),
        "baseline_mood": BASELINE_MOOD,
        "baseline_energy": BASELINE_ENERGY,
    }
