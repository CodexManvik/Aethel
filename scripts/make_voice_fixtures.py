"""Generate the E3 voice fixtures with Kokoro, so no recordings are needed: 20 complete utterances (one segment) and 20
mid-thought ones (two segments with a 700-1100 ms pause between them). 16 kHz mono PCM16 WAVs plus fixtures.json,
written to ~/.aethel/eval/voice/ (generated, not committed). Usage: py -3.11 scripts/make_voice_fixtures.py"""
import json
import random
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from aethel.paths import aethel_home  # noqa: E402
from aethel.voice.tts import KokoroTTS, VoiceBlend  # noqa: E402

COMPLETE = [
    "What time does the library open on Saturday?", "Can you remind me to call my sister tonight?",
    "Tell me a fun fact about octopuses.", "What's the weather like this weekend?", "How do I boil an egg properly?",
    "Thanks, that was really helpful.", "Open Notepad and write a shopping list.", "What did we talk about yesterday?",
    "I finished my first chapter today.", "Can you summarise the article I saved?", "Good morning, how are you?",
    "Play something calm while I work.", "How far is Leeds from Manchester?", "That's not quite what I meant.",
    "Explain what a neural network is.", "Set a timer for ten minutes.", "I'd like a coffee, please.",
    "What's the capital of Australia?", "Let's pick this up again tomorrow.", "Goodbye for now.",
]
MID_THOUGHT = [
    ("So I was thinking that", "we could go to the coast this weekend."), ("I need eggs, milk, and", "some bread for tomorrow."),
    ("The thing is", "I haven't started the report yet."), ("What if we", "moved the meeting to Friday?"),
    ("Can you open the", "budget spreadsheet from last month?"), ("I was going to say that", "the draft looks good to me."),
    ("Because the last time I tried it", "the whole thing crashed."), ("My sister's birthday is on the", "fourteenth of March."),
    ("Could you maybe", "read me the summary again?"), ("I went to the shop and", "they had run out of bread."),
    ("Actually, wait, let me", "think about that for a second."), ("Tell me about the", "history of the printing press."),
    ("Right so what I need is", "a list of all my open tasks."), ("Do you think it would be better if", "we started earlier?"),
    ("For the first slide I want", "a title and a short subtitle."), ("When I was in Leeds I met", "an old friend from school."),
    ("I can't remember if it was", "before or after lunch."), ("Can you remind me to call", "the dentist on Monday?"),
    ("The email should say that", "we've received the parcel."), ("Let's see, there's the budget and", "the timeline to finish."),
]
RATE = 16000


def to_16k(speech) -> np.ndarray:
    audio = np.frombuffer(speech.audio, dtype=np.int16).astype(np.float32)
    n = int(len(audio) * RATE / speech.sample_rate)
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.int16)


def write_wav(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())


def main() -> None:
    out = aethel_home() / "eval" / "voice"
    out.mkdir(parents=True, exist_ok=True)
    tts, rng = KokoroTTS(), random.Random(7)
    voices = ["af_heart", "bf_emma", "am_adam", "bm_george"]
    fixtures = []

    def segment(name: str, text: str, voice: str) -> dict:
        samples = to_16k(tts.synthesize(text, VoiceBlend.parse({voice: 1.0})))
        write_wav(out / name, samples)
        return {"file": name, "text": text, "ms": round(len(samples) / RATE * 1000)}

    for i, text in enumerate(COMPLETE):
        fixtures.append({"id": f"c{i:02d}", "kind": "complete", "gap_ms": None,
                         "segments": [segment(f"c{i:02d}.wav", text, voices[i % 4])]})
    for i, (a, b) in enumerate(MID_THOUGHT):
        voice = voices[i % 4]
        fixtures.append({"id": f"m{i:02d}", "kind": "mid_thought", "gap_ms": rng.randint(700, 1100),
                         "segments": [segment(f"m{i:02d}a.wav", a, voice), segment(f"m{i:02d}b.wav", b, voice)]})
    (out / "fixtures.json").write_text(json.dumps(fixtures, indent=2), encoding="utf-8")
    print(f"wrote {len(fixtures)} fixtures to {out}")


if __name__ == "__main__":
    main()
