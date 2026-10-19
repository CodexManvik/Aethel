"""The tone engine (ported from backend/emotion_engine.py): a conversation's running mood (unpleasant ↔ pleasant)
and energy (low ↔ high), after Russell's circumplex. Each user message nudges it, and it decays back to baseline.
Labels describe the conversation, not a claim about machine feelings: they drive a quiet accent glow in the UI and
one line in the companion's prompt.

Classification: a local ONNX emotion classifier (distilroberta, 7 labels) when models/emotion/ holds model.onnx and
tokenizer.json; otherwise a small word list, which needs nothing and is enough for the direction of a message."""
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from ..paths import MODELS_DIR

log = logging.getLogger("aethel.tone")
DELTA = 0.35        # how far one message moves the state toward its point
DECAY = 0.05        # how much of the way back to baseline each turn goes first
NEUTRAL_BAND = 0.18
LABELS = ["anger", "disgust", "fear", "joy", "neutral", "sadness", "surprise"]
COORDS = {"joy": (0.8, 0.4), "surprise": (0.6, 0.6), "anger": (-0.8, 0.8), "fear": (-0.5, 0.6),
          "disgust": (-0.6, 0.2), "sadness": (-0.7, -0.4), "neutral": (0.0, 0.0)}
_POSITIVE = {"thanks", "thank", "great", "awesome", "love", "nice", "good", "happy", "perfect", "excellent", "amazing",
             "glad", "appreciate", "cool", "yes", "wonderful", "brilliant", "helpful", "works", "worked", "fixed",
             "fun", "haha", "lol", "yay", "beautiful", "cute", "excited"}
_NEGATIVE = {"hate", "awful", "terrible", "bad", "wrong", "broken", "annoying", "stupid", "useless", "fail", "failed",
             "angry", "sad", "sorry", "confused", "frustrating", "frustrated", "worse", "worst", "cant", "can't",
             "won't", "upset", "lonely", "tired", "stressed", "anxious", "hurt", "cry", "crying"}
_HIGH = {"now", "urgent", "quickly", "asap", "immediately", "wow", "amazing", "hate", "angry", "excited", "hurry",
         "fast", "omg", "yay", "can't", "wait"}
_LOW = {"tired", "sleepy", "later", "whenever", "maybe", "boring", "bored", "slow", "calm", "quiet", "relax", "meh",
        "fine", "exhausted", "chill"}


@dataclass
class Tone:
    mood: float = 0.0
    energy: float = 0.0

    @property
    def label(self) -> str:
        return label_for(self.mood, self.energy)

    def to_dict(self) -> dict:
        return {**asdict(self), "label": self.label}


def label_for(mood: float, energy: float) -> str:
    if abs(mood) < NEUTRAL_BAND and abs(energy) < NEUTRAL_BAND:
        return "Neutral"
    if mood >= 0:
        if energy > 0.5:
            return "Enthusiastic"
        if mood > 0.5:
            return "Warm"
        if energy < -0.5:
            return "Relaxed"
        return "Calm" if energy < 0 else "Positive"
    if energy > 0.5:
        return "Tense"
    if mood < -0.5:
        return "Downcast"
    return "Flat" if energy < 0 else "Guarded"


def lexicon_point(text: str) -> tuple[float, float]:
    words = set(re.findall(r"[a-z']+", text.lower()))
    pos, neg = len(words & _POSITIVE), len(words & _NEGATIVE)
    hi, lo = len(words & _HIGH), len(words & _LOW)
    mood = 0.0 if pos + neg == 0 else (pos - neg) / (pos + neg)
    energy = 0.0 if hi + lo == 0 else (hi - lo) / (hi + lo)
    if "!" in text:
        energy = min(1.0, energy + 0.3)
    return mood, energy


class _Classifier:
    """The ONNX emotion model, loaded on first use; None from point() means "use the word list"."""

    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self._lock = threading.Lock()
        self._loaded = False
        self._session = self._tokenizer = None

    def _load(self) -> bool:
        with self._lock:
            if not self._loaded:
                self._loaded = True
                model, tok = self.model_dir / "model.onnx", self.model_dir / "tokenizer.json"
                if model.is_file() and tok.is_file():
                    try:
                        import onnxruntime as ort
                        from tokenizers import Tokenizer
                        self._tokenizer = Tokenizer.from_file(str(tok))
                        self._tokenizer.enable_truncation(128)
                        self._session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
                        log.info("tone: the ONNX emotion classifier is loaded")
                    except Exception as exc:
                        log.warning("tone: the emotion classifier couldn't load (%r); using the word list", exc)
                        self._session = None
        return self._session is not None

    def point(self, text: str) -> tuple[float, float] | None:
        if not self._load():
            return None
        enc = self._tokenizer.encode(text)
        feeds = {"input_ids": np.array([enc.ids], dtype=np.int64),
                 "attention_mask": np.array([enc.attention_mask], dtype=np.int64)}
        names = {i.name for i in self._session.get_inputs()}
        logits = self._session.run(None, {k: v for k, v in feeds.items() if k in names})[0][0]
        return COORDS.get(LABELS[int(np.argmax(logits))], COORDS["neutral"])


class ToneEngine:
    def __init__(self, model_dir: Path | None = None):
        self.classifier = _Classifier(model_dir or Path(os.environ.get("AETHEL_EMOTION_DIR", MODELS_DIR / "emotion")))

    def point(self, text: str) -> tuple[float, float]:
        try:
            got = self.classifier.point(text)
        except Exception as exc:
            log.warning("tone: classifying failed (%r); using the word list", exc)
            got = None
        return got if got is not None else lexicon_point(text)

    def step(self, state: Tone, text: str, baseline: Tone = Tone()) -> Tone:
        """Decay toward the baseline, then move toward the message's point; both stay within -1..1."""
        mood = state.mood + (baseline.mood - state.mood) * DECAY
        energy = state.energy + (baseline.energy - state.energy) * DECAY
        if text.strip():
            pm, pe = self.point(text)
            mood += pm * DELTA
            energy += pe * DELTA
        clamp = lambda v: round(max(-1.0, min(1.0, v)), 3)  # noqa: E731
        return Tone(clamp(mood), clamp(energy))


def prompt_line(tone: Tone) -> str | None:
    """For the volatile tail of a companion's prompt; nothing while the conversation is neutral."""
    if tone.label == "Neutral":
        return None
    return f"The conversation's tone right now: {tone.label.lower()}. Let it colour your reply naturally."
