"""E3: call latency and false cut-offs (voice spec §14). Plays the fixtures from scripts/make_voice_fixtures.py into
/ws/voice in real time, as the call view would (speech_start, speech_end + audio at the segment boundaries, the real
pause between a mid-thought fixture's two halves), and measures at the client:

  latency:      end of the user's last segment -> the first audio frame of the reply (p50 / p95)
  false cut-off: a mid-thought fixture whose reply began before its second half was spoken

Two conditions: VAD only (voice.system1_end_of_turn off: every turn waits max_wait_ms) and VAD + System 1.

It runs its own backend in this process, in a throwaway ~/.aethel, with a scripted LLM (no tokens) and --stt fake
(the fixture's own text, instantly, so the numbers are turn-taking + synthesis) or --stt local (the faster-whisper
model). Azure and a real LLM are not wired in: they spend money or tokens, so a run with them needs the owner's
go-ahead first. Writes ~/.aethel/eval/voice.json.
Usage: py -3.11 scripts/eval_voice.py [--stt fake|local] [--limit N]"""
import argparse
import asyncio
import json
import os
import socket
import statistics
import sys
import tempfile
import time
import wave
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
REAL_HOME = Path(os.environ.get("AETHEL_HOME", str(Path.home() / ".aethel")))


# ---- metrics (tested on synthetic timelines) ------------------------------------------------------------------
def percentile(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    k = (len(s) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize(runs: list[dict]) -> dict:
    """runs: {kind, last_speech_end, first_audio_at, early_audio_at (mid-thought: reply audio before the second half)}
    in seconds on one clock. A reply has started at its first audio frame (acknowledgements don't count)."""
    latencies = [r["first_audio_at"] - r["last_speech_end"] for r in runs
                 if r.get("first_audio_at") is not None and r["first_audio_at"] >= r["last_speech_end"]]
    mids = [r for r in runs if r["kind"] == "mid_thought"]
    cut = [r for r in mids if r.get("early_audio_at") is not None]
    return {"n": len(runs), "answered": len(latencies),
            "latency_p50_ms": None if not latencies else round(percentile(latencies, 50) * 1000),
            "latency_p95_ms": None if not latencies else round(percentile(latencies, 95) * 1000),
            "false_cutoff_rate": None if not mids else round(len(cut) / len(mids), 3), "mid_thought": len(mids)}


# ---- the simulated caller -----------------------------------------------------------------------------------
def read_pcm(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        return w.readframes(w.getnframes())


async def call_one(url: str, conversation_id: str, fixture: dict, folder: Path, timeout_s: float = 60) -> dict:
    import websockets
    run = {"id": fixture["id"], "kind": fixture["kind"], "first_audio_at": None, "early_audio_at": None}
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "hello", "conversation_id": conversation_id}))
        while json.loads(await ws.recv()).get("type") != "ready":
            pass
        audio_seen = asyncio.Event()
        done = asyncio.Event()
        state = {"speaking_second": False}

        async def reader():
            async for msg in ws:
                if isinstance(msg, bytes):
                    if int.from_bytes(msg[:4], "little", signed=True) < 0:
                        continue  # an acknowledgement, not the reply
                    now = time.monotonic()
                    if fixture["kind"] == "mid_thought" and not state["speaking_second"] and run["early_audio_at"] is None:
                        run["early_audio_at"] = now
                    if state["speaking_second"] or fixture["kind"] == "complete":
                        if run["first_audio_at"] is None:
                            run["first_audio_at"] = now
                            audio_seen.set()
                elif json.loads(msg).get("type") == "turn_end" and (state["speaking_second"] or fixture["kind"] == "complete"):
                    done.set()

        reading = asyncio.ensure_future(reader())
        try:
            for i, seg in enumerate(fixture["segments"]):
                if i == 1:
                    await asyncio.sleep(fixture["gap_ms"] / 1000)
                    state["speaking_second"] = True
                pcm = read_pcm(folder / seg["file"])
                await ws.send(json.dumps({"type": "speech_start"}))
                await asyncio.sleep(seg["ms"] / 1000)  # the user speaking it, in real time
                await ws.send(json.dumps({"type": "speech_end", "duration_ms": seg["ms"]}))
                await ws.send(pcm)
                run["last_speech_end"] = time.monotonic()
            await asyncio.wait_for(done.wait(), timeout_s)
        except asyncio.TimeoutError:
            run["timed_out"] = True
        finally:
            await ws.send(json.dumps({"type": "end_call"}))
            reading.cancel()
    return run


# ---- an in-process backend ----------------------------------------------------------------------------------
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Scripted:
    """The LLM: a short fixed reply, streamed like a fast provider."""
    def __init__(self):
        self.label = "scripted:eval"

    async def stream(self, messages, *, temperature, max_tokens, tools=None):
        from aethel.providers.base import StreamDone, TextDelta
        for chunk in ("Sure, I can help with that. ", "Here is what I found."):
            await asyncio.sleep(0.05)
            yield TextDelta(chunk)
        yield StreamDone("stop")

    async def list_models(self):
        return ["eval"]


class FixtureSTT:
    """--stt fake: the fixture's own words, instantly (isolates turn-taking and synthesis from recognition)."""
    name = "fake:fixture-text"

    def __init__(self, texts: dict[bytes, str]):
        self.texts = texts

    def transcribe(self, pcm16, sample_rate=16000):
        from aethel.voice.stt import Transcript
        return Transcript(self.texts.get(pcm16[:4000], ""), "en", len(pcm16) / 32000, 0, self.name)


async def run_inprocess(fixtures: list[dict], folder: Path, stt_mode: str) -> dict:
    import uvicorn
    os.environ["AETHEL_HOME"] = tempfile.mkdtemp(prefix="aethel-e3-")
    os.environ.update(AETHEL_MCP="0", AETHEL_DEV="1", AETHEL_LAYA_DIR=str(REAL_HOME / "models" / "laya"))
    from aethel.app import create_app
    from aethel.services import build_services
    svc = build_services(provider_factory=lambda entry, key, settings: Scripted(), local_llm=None)
    svc.settings.update({"roles": {"chat": [{"provider": "custom", "model": "eval"}]},
                         "custom_base_url": "http://127.0.0.1:9/v1", "voice": {"stt": "local", "ack": False}})
    if stt_mode == "fake":
        texts = {read_pcm(folder / s["file"])[:4000]: s["text"] for f in fixtures for s in f["segments"]}
        svc.voice.stt = lambda: FixtureSTT(texts)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(svc), host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.ensure_future(server.serve())
    while not server.started:
        await asyncio.sleep(0.1)
    for _ in range(600):  # System 1 loads in the background (from the real ~/.aethel's model)
        if svc.system1.status() in ("ready",) or svc.system1.status().startswith("failed"):
            break
        await asyncio.sleep(0.5)
    results = {"system1": svc.system1.status(), "stt": stt_mode}
    try:
        for name, on in (("vad_only", False), ("vad_system1", True)):
            svc.settings.update({"voice": {"system1_end_of_turn": on}})
            runs = []
            for f in fixtures:
                conv = svc.conversations.create()
                runs.append(await call_one(f"ws://127.0.0.1:{port}/ws/voice", conv.id, f, folder))
                print(f"  {name} {f['id']}: {runs[-1]}", flush=True)
            rows = svc.db.query("SELECT * FROM voice_turns ORDER BY created_at")[-len(runs):] if runs else []
            stages = {k: percentile([r[k] for r in rows if r[k] is not None], 50)
                      for k in ("transcript_at", "decision_at", "first_token_at", "first_audio_at")}
            results[name] = {"summary": summarize(runs), "server_p50_s": stages, "runs": runs}
    finally:
        server.should_exit = True
        await serving
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stt", choices=["fake", "local"], default="fake")
    ap.add_argument("--limit", type=int, default=0, help="fixtures of each kind (0: all)")
    args = ap.parse_args()
    folder = REAL_HOME / "eval" / "voice"
    fixtures = json.loads((folder / "fixtures.json").read_text(encoding="utf-8"))
    if args.limit:
        fixtures = [f for f in fixtures if f["kind"] == "complete"][: args.limit] + \
                   [f for f in fixtures if f["kind"] == "mid_thought"][: args.limit]
    results = asyncio.run(run_inprocess(fixtures, folder, args.stt))
    out = REAL_HOME / "eval" / "voice.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    for name in ("vad_only", "vad_system1"):
        print(name, results[name]["summary"], "server stages p50 (s after speech end):", results[name]["server_p50_s"])
    print(f"System 1: {results['system1']}  wrote {out}")


if __name__ == "__main__":
    main()
