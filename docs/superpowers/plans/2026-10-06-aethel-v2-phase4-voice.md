# Aethel v2 · Phase 4 (Voice: Call Mode): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Execution:** inline. Two PRs: **4a (voice engine, backend)** on branch `v2-phase4a`, then **4b (call view, frontend)** on `v2-phase4b`.

**Goal:** A hands-free call with the persona. You talk; Aethel decides when you've finished your thought, answers in its own voice sentence by sentence, and stops the moment you interrupt. Tasks started in a call are narrated, and the whole call is saved as normal messages.

**Architecture:**
- The **webview** captures the mic (echo cancellation and noise suppression on). It runs Silero VAD (`@ricky0123/vad-web`) and sends each utterance as 16 kHz PCM16 over `/ws/voice`.
- The **backend**, per utterance:
  1. Transcribes it with local faster-whisper (or Groq Whisper, optional).
  2. Asks System 1 whether the thought is complete, and waits or replies (§A4).
  3. Runs the normal chat turn (memory, routing, tasks), streaming tokens into a sentence chunker.
  4. Synthesises each sentence with Kokoro, using the persona's voice blend, as soon as it's complete, and streams it back with a sentence id.
- **Barge-in:** the client stops playback at once and reports the last sentence it played. The server cancels generation and TTS, and truncates the saved reply to what was actually heard.

**Tech Stack:** faster-whisper 1.2 (already installed, `models/stt` has `small`), kokoro-onnx 0.5 (54 voices; `create(text, voice=ndarray)` takes a blended style), edge-tts (hybrid fallback only), FastAPI WebSocket, `@ricky0123/vad-web` + `onnxruntime-web` (new, pinned), AudioWorklet, Zustand.

**Spec:** parent spec §8 (call mode), §11 (`/ws/voice`), §12.3 (call view), §13 (voice tests), §14 E3. Roadmap decisions D2, D3 and D7.

## Global Constraints
- Every earlier constraint still applies (roadmap §6).
- **Private mode:** STT is local, TTS is Kokoro only (edge-tts is a cloud service, so it's forbidden), and the LLM is local.
- **Speech-to-text defaults to local** (D2). Groq Whisper is opt-in in Settings → Voice.
- **The latency target is measured, not promised** (D3): E3 reports p50/p95 per configuration.
- **The call transcript is ordinary messages:** user and assistant, `meta.modality = "voice"`. Memory extraction, episodic indexing and intent routing all apply as they do to typed messages.
- **Barge-in truncation is exact:** the saved assistant text equals the concatenation of the sentences whose playback had **started** when the interrupt came. `meta.interrupted = true`.
- **The end-of-turn threshold is measured** on a labelled set with a dev/test split (Task A7). Until then it's provisional at 0.6, as the spec says.
- **Audio never touches disk,** except E3 fixtures and an explicit "save recording" (not in scope).

---

# Part A: The voice engine (PR 4a)

## File map (Part A)
| File | Change |
|---|---|
| `backend/aethel/voice/__init__.py` | package |
| `backend/aethel/voice/stt.py` | `SpeechToText` protocol, `LocalWhisper`, `GroqWhisper`, `pcm16_to_wav()` |
| `backend/aethel/voice/tts.py` | `VoiceBlend`, `KokoroTTS`, `EdgeTTS`, `speakable()`, `TTS.synthesize()` |
| `backend/aethel/voice/chunker.py` | `SentenceChunker` |
| `backend/aethel/voice/turns.py` | `EndOfTurn` judge, `TurnManager` state machine |
| `backend/aethel/voice/session.py` | `VoiceSession`: one call; glues STT → turns → chat → TTS; barge-in |
| `backend/aethel/voice/protocol.py` | voice WS message models (client/server unions), generated to TS |
| `backend/aethel/api/voice_ws.py` | `/ws/voice` route (auth, origin, one session per socket) |
| `backend/aethel/api/routes/voice.py` | `GET /api/voice/status`, `GET /api/voice/voices`, `POST /api/voice/preview` |
| `backend/aethel/chat/service.py` | `run_turn(event, sink)`: a turn whose text deltas also go to a sink; returns the message ids |
| `backend/aethel/store/migrations/015_voice_turns.sql` | per-turn latency record (E3) |
| `backend/aethel/settings.py` | `VoiceSettings` |
| `backend/aethel/services.py`, `app.py` | wiring; lazy warm-up when a call starts |
| `backend/aethel/eval/s1_eot.jsonl`, `scripts/eval_s1_eot.py` | end-of-turn labelled set + measurement |
| `backend/tests/test_stt.py`, `test_tts.py`, `test_chunker.py`, `test_turns.py`, `test_voice_session.py`, `test_voice_ws.py` | tests |

### Task A1: Speech to text
**Produces:**
```python
@dataclass
class Transcript:
    text: str
    language: str | None
    duration_s: float
    latency_ms: int

class SpeechToText(Protocol):
    name: str
    def transcribe(self, pcm16: bytes, sample_rate: int = 16000) -> Transcript: ...   # blocking: call in a thread

def pcm16_to_wav(pcm16: bytes, sample_rate: int = 16000) -> bytes                    # 44-byte RIFF header + data

class LocalWhisper:           # name "local:faster-whisper-<size>"
    def __init__(self, size: str = "small", device: str = "cpu", compute_type: str = "int8",
                 model_dir: Path = PROJECT_ROOT / "models" / "stt"): ...
    def warm(self) -> None    # loads the model (first call downloads it if missing)
    def transcribe(...)       # np.frombuffer(pcm16, int16).astype(float32) / 32768 → model.transcribe(audio,
                              #   beam_size=1, vad_filter=False, language=None, condition_on_previous_text=False)
class GroqWhisper:            # name "groq:whisper-large-v3-turbo"
    def __init__(self, keys: KeyStore, http: httpx.Client): ...
    def transcribe(...)       # POST https://api.groq.com/openai/v1/audio/transcriptions, multipart: file=("a.wav", wav),
                              #   model=whisper-large-v3-turbo, response_format=json, temperature=0; 30 s timeout
def make_stt(settings: AppSettings, keys, http) -> SpeechToText   # private mode or settings.voice.stt == "local" → LocalWhisper
```
- Beam size 1 and no internal VAD: the client already cut the utterance, and speed matters more than the last fraction of accuracy in conversation. It's revisited in E3 if the word error looks bad.
- Errors surface as a `SpeechToTextError` with a user-readable message: "Speech recognition isn't available: …".

- [ ] **Tests** (`test_stt.py`):
  - The `pcm16_to_wav` header is correct (RIFF, 16 kHz, mono, 16-bit, data length).
  - `make_stt` picks local in private mode even when the setting says groq.
  - `GroqWhisper` against an httpx `MockTransport`: the multipart field names, model and auth header, and the text is parsed.
  - `LocalWhisper` with a monkeypatched `WhisperModel` fake: the float conversion range is −1..1, and the segments are joined.
  - A real-model test behind `AETHEL_VOICE_IT=1`: transcribe a Kokoro-generated "hello there" and assert "hello" is in the text.
- [ ] Implement, run, then **commit** `feat(voice): speech to text (local faster-whisper, optional Groq)`.

### Task A2: Text to speech with voice blends
**Produces:**
```python
@dataclass(frozen=True)
class VoiceBlend:
    weights: tuple[tuple[str, float], ...]   # e.g. (("af_heart", 0.7), ("bf_emma", 0.3)); normalised to sum 1
    speed: float = 1.0
    @staticmethod
    def parse(d: dict) -> "VoiceBlend"       # {"af_heart": 0.7, "bf_emma": 0.3, "speed": 1.0}; unknown voices rejected

@dataclass
class Speech:
    audio: bytes          # pcm16 mono, or mp3 for edge-tts
    codec: Literal["pcm16", "mp3"]
    sample_rate: int      # 24000 for Kokoro
    duration_s: float

def speakable(text: str) -> str
    # strip markdown (*_`#>), links → their text, bare URLs → "a link", [n] citation markers, code blocks →
    # "(code)", emoji; collapse whitespace. Ported from the legacy clean_text_for_speech, plus citations.

class KokoroTTS:
    def __init__(self, model_dir: Path = PROJECT_ROOT / "models" / "tts"): ...
    def voices(self) -> list[str]
    def style(self, blend: VoiceBlend) -> np.ndarray          # Σ w_i · get_voice_style(name_i), cached per blend
    def synthesize(self, text: str, blend: VoiceBlend) -> Speech   # create(text, voice=style, speed, lang from the
                                                                  # first voice's prefix: a→en-us, b→en-gb, …)
class EdgeTTS:            # hybrid only: edge_tts.Communicate(text, voice="en-GB-SoniaNeural") → mp3 bytes
    async def synthesize(self, text: str) -> Speech
class TTS:
    async def synthesize(self, text: str, blend: VoiceBlend, private: bool) -> Speech | None
        # Kokoro (in a thread); on failure, edge-tts unless private; None if both fail (logged)
```
- Kokoro files are `models/tts/kokoro-v1.0.onnx` and `voices-v1.0.bin`. They're already present, and the legacy `ensure_kokoro_models()` download logic is ported for fresh installs.
- The language codes come from the voice-name prefix (`a`, `b`, `e`, `f`, `h`, `i`, `j`, `p`, `z`), the same as kokoro's own map.

- [ ] **Tests** (`test_tts.py`):
  - `VoiceBlend.parse` normalises weights, rejects unknown voices and negative weights, and the speed is clamped to 0.5–2.0.
  - `speakable` handles markdown, links, `[3]` citations, code blocks and emoji.
  - `KokoroTTS.style` on a fake voices table is the weighted sum, and it's cached.
  - The `TTS` fallback order: Kokoro raising → edge used in hybrid, and **never** in private (returns None).
  - A real-Kokoro test behind `AETHEL_VOICE_IT=1`: "Hello." gives > 0.3 s of 24 kHz audio, and a blend of two voices differs from either alone (RMS difference > 0).
- [ ] Implement, run, then **commit** `feat(voice): Kokoro text to speech with voice blends`.

### Task A3: The sentence chunker
**Produces:**
```python
class SentenceChunker:
    def __init__(self, min_chars: int = 12, max_chars: int = 220): ...
    def feed(self, delta: str) -> list[str]   # complete sentences ready to speak
    def flush(self) -> list[str]              # whatever is left at the end of the reply
```
**Rules:**
- A sentence ends at `.`, `!`, `?` or `…` followed by whitespace, or at a newline.
- **Not** at decimals (`3.5`), common abbreviations (`Mr.`, `Mrs.`, `Dr.`, `e.g.`, `i.e.`, `etc.`, `vs.`), or initials (`J. K.`).
- A sentence shorter than `min_chars` is merged with the next one ("Okay." + "Let's go.").
- A sentence longer than `max_chars` is split at the last comma or semicolon before the limit, otherwise at a space.
- Markdown list items and headings end at newlines.

- [ ] **Tests:**
  - Streaming in random-sized deltas gives the same sentences as one big delta (property-style, over 20 seeds).
  - Each rule above has its own case.
  - `flush` returns the tail.
- [ ] Implement, then **commit** `feat(voice): sentence chunker for streamed speech`.

### Task A4: End-of-turn
**Produces:**
```python
EOT_Q = "Has the user finished saying what they wanted to say, so it's Aethel's turn to answer?"
def eot_state(utterance: str, previous_reply: str | None) -> dict
class EndOfTurn:
    def __init__(self, system1, settings): ...
    async def finished(self, utterance: str, previous_reply: str | None) -> float | None   # P(yes), None without S1

@dataclass
class TurnDecision:
    text: str                      # the whole utterance so far
    waited_ms: int
    eot_p: float | None
class TurnManager:
    """One call's turn state. Clock and sleeps are injected, for tests."""
    def __init__(self, eot: EndOfTurn, *, threshold: float, short_wait_ms: int = 300, max_wait_ms: int = 1500,
                 sleep=asyncio.sleep): ...
    async def on_utterance(self, text: str, previous_reply: str | None) -> None
        # a transcribed utterance arrived (after speech_end). Appends to the pending text.
    def on_speech_start(self) -> None   # the user started speaking again: cancel any pending decision
    async def next_turn(self) -> TurnDecision  # waits until it's Aethel's turn:
        # p ≥ threshold → wait short_wait_ms of silence, then go
        # p < threshold or None → wait up to max_wait_ms; speech_start cancels the wait (more is coming);
        # if no speech starts in time, go anyway
```
- With System 1 off or still loading, `p` is None, so every turn waits for `max_wait_ms` of silence. That's VAD-only behaviour: E3's baseline condition.

- [ ] **Tests** (`test_turns.py`, with a fake sleep driven by a manual clock):
  - p = 0.9 → goes after 300 ms.
  - p = 0.2 and no new speech → goes after 1500 ms.
  - p = 0.2, then speech_start at 600 ms, then a second utterance → **one** turn with both texts joined, and the p is re-asked on the joined text.
  - No System 1 → 1500 ms.
  - `eot_state` includes the previous reply truncated to 400 chars.
- [ ] Implement, then **commit** `feat(voice): end-of-turn with System 1`.

### Task A5: `ChatService.run_turn` with a sink
**Produces:**
```python
TextSink = Callable[[str], Awaitable[None]]
@dataclass
class TurnResult:
    user_message_id: str
    assistant_message_id: str
    status: Literal["complete", "stopped", "error"]
async def run_turn(self, event: UserMessage, *, sink: TextSink | None = None, meta: dict | None = None) -> TurnResult
```
- Today's `start_turn` becomes `create_task(self.run_turn(event))`, so typed chat is unchanged.
- `_run_turn` calls `await sink(delta)` after publishing each `Token`.
- `meta` is merged into both the user and assistant message meta (e.g. `{"modality": "voice"}`).
- `stop(message_id)` works the same.

- [ ] **Tests:**
  - A sink receives exactly the streamed deltas in order.
  - Voice meta is stored on both messages.
  - Existing chat tests are unchanged (green).
  - A sink that raises doesn't kill the turn: it's logged and the turn completes.
- [ ] Implement, then **commit** `refactor(chat): run a turn with a text sink`.

### Task A6: The voice session and `/ws/voice`
**Protocol** (`voice/protocol.py`, Pydantic, `type` discriminator, exported to TS through the existing codegen as `VoiceProtocol`):

| Direction | Message | Fields |
|---|---|---|
| client → server | `hello` | `conversation_id` |
| client → server | `speech_start` | — |
| client → server | **binary frame** | an utterance's PCM16 16 kHz mono, sent once after `speech_end` |
| client → server | `speech_end` | `duration_ms` |
| client → server | `interrupt` | `last_started_sentence_id: int \| null` |
| client → server | `end_call` | — |
| server → client | `ready` | `stt: str`, `tts_voice: str`, `persona_name: str` |
| server → client | `caption` | `role: user\|assistant`, `text`, `final: bool`, `message_id: str \| null` |
| server → client | `thinking` | — (after the turn decision, before the first audio) |
| server → client | `tts_meta` | `sentence_id: int`, `text`, `codec`, `sample_rate`, `turn_id` |
| server → client | **binary frame** | 4-byte little-endian `sentence_id` + audio bytes (follows its `tts_meta`) |
| server → client | `turn_end` | `turn_id`, `status` |
| server → client | `voice_error` | `message`, `fatal: bool` |

**`VoiceSession` lifecycle (one per socket):**
1. **`hello`:** check the conversation exists. Resolve the voice blend (Phase 5: the persona's; for now `settings.voice.default_blend`). Warm STT and TTS in threads. Send `ready`.
2. **Utterance** (binary after `speech_end`):
   1. `stt.transcribe` in a thread.
   2. Send a user `caption` (final).
   3. `turns.on_utterance(text, last_reply)`.
3. **The turn loop** (a task): `decision = await turns.next_turn()`, then:
   1. `route()` it (the same intent routing as typed messages).
   2. A **task** → `engine.start(...)`, with a short spoken acknowledgement: "On it." (synthesised, not LLM).
   3. A **stop** → cancel the tasks and say "Stopped."
   4. A **chat** turn → `chat.run_turn(UserMessage(text=decision.text), sink=self._on_delta, meta={"modality": "voice"})`.
4. **`_on_delta`:**
   1. `chunker.feed(delta)`.
   2. Each sentence gets an incrementing `sentence_id` and goes into an `asyncio.Queue`.
   3. A **TTS worker** synthesises sentences in order, sends `tts_meta` + the binary frame, and records `first_audio_at` for the latency row.
   4. At the end, `chunker.flush()`.
5. **`interrupt`:**
   1. Set `interrupted`.
   2. `chat.stop(assistant_message_id)`.
   3. Drain the TTS queue and cancel the worker's current synthesis.
   4. Once the turn ends: `messages.update(assistant_id, content=" ".join(texts[≤ last_started_sentence_id]), status="stopped", meta={…, "interrupted": True})`.
   5. Send `turn_end(status="interrupted")`.
6. **Ack** (`settings.voice.ack`, default on): if no token arrives within 600 ms of the turn starting, play a cached acknowledgement. It's pre-synthesised at `hello`, one per blend from `["Mm.", "Okay—", "Right."]`, rotating. It's not saved in the transcript.
7. **The latency row** (`voice_turns`):
   ```sql
   CREATE TABLE voice_turns (
     id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, user_message_id TEXT, assistant_message_id TEXT,
     speech_end_at REAL, transcript_at REAL, decision_at REAL, first_token_at REAL, first_audio_at REAL,
     eot_p REAL, waited_ms INTEGER, stt TEXT, interrupted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
   );
   ```
   The times are `time.monotonic()` seconds relative to `speech_end_at`. It's used by E3 and Settings → Voice ("last call: 1.4 s to first audio").
8. **`end_call`** or a disconnect: cancel everything, then close.

**Route** (`api/voice_ws.py`): the same origin check and token check as `/ws/session`. One active call per conversation: a second socket for the same conversation gets `voice_error(fatal)` "This conversation is already in a call."

- [ ] **Tests** (`test_voice_session.py` with fake STT/TTS/S1/LLM; `test_voice_ws.py` through TestClient):
  - The happy path: hello → utterance → caption(user) → thinking → tts_meta + binary per sentence in order → turn_end(complete). The user and assistant messages are saved with `modality: voice`.
  - Barge-in: the fake LLM streams 5 sentences slowly; interrupt with `last_started_sentence_id=1` → the stored assistant text is exactly sentences 0–1, `meta.interrupted` is set, the LLM stream is cancelled (no more deltas consumed), and no `tts_meta` arrives after the interrupt.
  - A mid-thought pause: two utterances 600 ms apart with p = 0.2 → **one** user message with both texts.
  - The ack plays when the first token is slow (fake LLM sleeps 1 s) and not when it's fast.
  - Task routing: a fake router says task → `engine.start` is called and "On it." is spoken; no chat turn.
  - Private mode: TTS never calls edge-tts.
  - A second socket on the same conversation is refused.
  - Auth and origin as for `/ws/session`.
  - The `voice_turns` row has monotonic, ordered timestamps.
- [ ] Implement, run the full suite, then **commit** `feat(voice): /ws/voice call sessions with barge-in`.

### Task A7: Measure the end-of-turn threshold
- **`backend/aethel/eval/s1_eot.jsonl`:** 100 hand-written utterance transcripts, interleaved 50/50.
  - **`finished`:** complete questions, statements, answers, "yeah", "no thanks".
  - **`not_finished`:** "so I was thinking that", "and then I", "the thing is", "what if we", trailing "um", "because", a list in progress ("I need eggs, milk, and").
  - Each row has an optional `previous_reply`.
- **`scripts/eval_s1_eot.py`:** reuses the intent script's helpers. The rule: **the lowest threshold whose dev precision for `finished` is ≥ 0.9**, because a false "finished" interrupts the user, which is worse than waiting 1.5 s. It reports held-out P/R and ECE, and tries up to 3 wordings on dev only. It writes `~/.aethel/eval/s1_eot.json`.
- **`VoiceSettings.eot_threshold`** gets the measured default and a comment, in the style of `System1Settings`.
- [ ] Test the pick rule on a toy set, run with the real Laya (CPU, no tokens spent), record the result, then **commit** `eval(voice): measure the end-of-turn threshold`.

### Task A8: Settings, API, verify, PR 4a
- **`VoiceSettings`:**
  - `stt: Literal["local", "groq"] = "local"`
  - `stt_model: str = "small"`
  - `eot_threshold: float` (measured)
  - `short_wait_ms: int = 300`
  - `max_wait_ms: int = 1500`
  - `ack: bool = True`
  - `narration: bool = True`
  - `default_blend: dict = {"af_heart": 1.0, "speed": 1.0}`
- **`GET /api/voice/status`:** `{stt: {name, loaded}, tts: {kokoro: bool, voices: int}, last_turn_ms: int | None}`.
- **`GET /api/voice/voices`:** Kokoro voice names grouped by language.
- **`POST /api/voice/preview`:** `{blend, text ≤ 200}` → `audio/wav`. Phase 5's blend editor uses it.
- [ ] Tests for the routes. Full suite, review subagent, fixes. Open the PR "Phase 4a: voice engine" and bind it.

---

# Part B: The call view (PR 4b)

## File map (Part B)
| File | Change |
|---|---|
| `frontend_app/package.json` | `@ricky0123/vad-web` + `onnxruntime-web` (pinned exact versions) |
| `frontend_app/scripts/copy-vad-assets.mjs`, `vite.config.ts` | copy `silero_vad_v5.onnx`, the VAD worklet and ort-wasm files into `public/vad/` at build/dev |
| `frontend_app/src/features/call/audio/capture.ts` | `startCapture()`: MicVAD with echo cancellation, noise suppression and auto gain; callbacks |
| `frontend_app/src/features/call/audio/playback.ts` | `Playback`: a sentence queue, PCM16/mp3 decoding, stop/flush, the current sentence id, an `AnalyserNode` |
| `frontend_app/src/features/call/voiceSocket.ts` | the `/ws/voice` client (JSON + binary frames, typed by the generated `VoiceProtocol`) |
| `frontend_app/src/stores/voice.ts` | the call state machine |
| `frontend_app/src/features/call/CallView.tsx`, `Portrait.tsx`, `Captions.tsx`, `TaskStrip.tsx`, `CallControls.tsx` | the call screen |
| `frontend_app/src/features/conversation/PromptBox.tsx` | mic button → start a call |
| `frontend_app/src/stores/ui.ts`, `features/shell/AppShell.tsx` | the `call` screen |
| `frontend_app/src-tauri/...` | mic permission (only if the spike shows it's needed) |
| `backend/aethel/voice/narrator.py` | task narration |
| `scripts/eval_voice.py`, `scripts/make_voice_fixtures.py` | the E3 harness |
| `frontend_app/src/features/settings/VoiceSection.tsx` | Settings → Voice |

### Task B1: Mic spike in the Tauri webview (D7)
- [ ] In `pnpm tauri dev`, add a temporary dev-only route that calls `navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}})`. Record:
  1. Whether WebView2 prompts.
  2. Whether the permission persists across restarts.
  3. Whether echo cancellation is on (`track.getSettings()`).
- **If it prompts and persists,** do nothing. **If it's denied or prompts on every start,** handle WebView2's `PermissionRequested` for the microphone in the Rust setup (allow only for our own origin). Document the code in `src-tauri/src/lib.rs`.
- **If capture in the webview isn't workable,** stop and switch to the fallback: backend capture via `sounddevice` plus Silero VAD ONNX in Python. Write that as an amendment to this plan before going on.
- [ ] Write the outcome into this plan's "Spike result" note, then remove the temporary route. **Commit** `chore(voice): microphone in the webview (spike result)`.

### Task B2: Capture and playback
**Produces (TS):**
```ts
// capture.ts
export interface Capture { stop(): Promise<void>; setMuted(m: boolean): void }
export function startCapture(cb: {
  onSpeechStart(): void;
  onSpeechEnd(pcm16: ArrayBuffer, durationMs: number): void;   // MicVAD gives Float32 16 kHz → Int16
  onLevel(rms: number): void;                                  // input level, for the visuals
}): Promise<Capture>;
export function floatTo16(samples: Float32Array): Int16Array;

// playback.ts
export class Playback {
  constructor(ctx?: AudioContext);
  enqueue(meta: { sentenceId: number; codec: "pcm16" | "mp3"; sampleRate: number }, audio: ArrayBuffer): void;
  stop(): void;                         // stop now, drop the queue
  get lastStartedSentenceId(): number | null;
  get playing(): boolean;
  analyser: AnalyserNode;               // output level, for the portrait rings
  onIdle?: () => void;                  // the queue ran dry
}
```
- MicVAD settings: `positiveSpeechThreshold 0.6`, `negativeSpeechThreshold 0.45`, `redemptionFrames 8` (~250 ms), `minSpeechFrames 4`. Asset paths point at `/vad/`.
- Playback starts each sentence when the previous one ends, with no gap. PCM16 goes through `AudioBuffer`; mp3 through `decodeAudioData`.

- [ ] **Tests (vitest):**
  - `floatTo16` clamps and scales.
  - `Playback` with a fake AudioContext: plays in order, `stop()` empties the queue and leaves `lastStartedSentenceId` at the playing one, an mp3 meta goes through `decodeAudioData`, and `onIdle` fires.
  - `copy-vad-assets` lists the expected files.
- [ ] Implement, then **commit** `feat(call): microphone capture with VAD; sentence playback`.

### Task B3: The voice store and socket
**Produces:**
```ts
type CallState = "idle" | "connecting" | "listening" | "hearing" | "thinking" | "speaking" | "ended" | "error";
interface VoiceState {
  state: CallState; conversationId: string | null; muted: boolean;
  captions: { role: "user" | "assistant"; text: string; final: boolean }[];   // the last 6
  error: string | null; outputLevel: number; inputLevel: number;
  startCall(conversationId: string): Promise<void>; endCall(): Promise<void>; toggleMute(): void;
}
```
- **Wiring:**
  - capture `onSpeechStart`: if playback is playing, `playback.stop()` and send `interrupt(lastStartedSentenceId)`. Either way, send `speech_start` and set the state to `hearing`.
  - `onSpeechEnd`: send `speech_end`, then the PCM binary, and set `thinking`.
  - `tts_meta` + binary → `playback.enqueue`, state `speaking`.
  - `turn_end` + playback `onIdle` → `listening`.
  - `caption` → captions.
  - The socket closing → `ended`.
- **Muted:** capture keeps running for the level meter, but `onSpeechStart`/`onSpeechEnd` are ignored.
- **Starting a call while one is active** in another conversation ends that one first.

- [ ] **Tests** (a fake socket and fake capture/playback):
  - The full transition sequence of a turn.
  - Barge-in sends `interrupt` with the right id **before** `speech_start`.
  - Mute ignores speech.
  - The captions buffer stays at the last 6.
  - The socket closing → `ended`.
- [ ] Implement, then **commit** `feat(call): voice store and socket`.

### Task B4: The call screen
**UI** (Paper & Ink, spec §12.3 item 2, the approved v3 mockup):
- **Portrait:** centred, 220 px.
  - Until Phase 5 it's the persona's monogram on paper.
  - The breathing rings are 3 concentric hairline circles. Their scale is `1 + 0.08 · level`, using the gentle spring (140/22); `level` is the playback analyser RMS when speaking, and the input level × 0.5 when hearing.
  - **Reduced motion:** opacity only.
- **Captions:** the assistant's current sentence in Newsreader 22 px, ink-settling (reusing `InkText`). The user's last words below it in Inter 13 px `text-muted`.
- **State line:** "Listening…", "Thinking…", or nothing while speaking. Shown as a small caps label above the controls.
- **TaskStrip:** when a task from this conversation is active, a compact one-line strip shows its goal, the current step and a progress pen-check. Clicking it opens the task panel.
- **Controls:** mute (`Mic`/`MicOff`), end call (accent, `PhoneOff`), show chat (returns to the conversation with the call still running; a small "In a call" pill in the header returns to it).
- **Keyboard:** Space toggles mute, Esc ends the call. These are announced in the hint line.
- **Entering:** the PromptBox gets a mic button next to send, labelled "Start a call". It starts the call for the current conversation, creating one if needed.

- [ ] **Tests (vitest):**
  - Rendering each state.
  - The rings respond to the level (style transform).
  - Space mutes and Esc ends.
  - The TaskStrip shows for an active task.
  - The PromptBox mic starts a call (a store spy).
  - Show chat keeps the call; the header pill returns.
- [ ] Implement, `pnpm test` + `pnpm build`, then **commit** `feat(call): the call view`.

### Task B5: Task narration
**Produces:**
```python
class Narrator:
    """Turns a call's task events into short spoken lines, at most one every 8 s."""
    def __init__(self, *, router, hub, settings, speak: Callable[[str], Awaitable[None]], min_gap_s: float = 8.0): ...
    def follow(self, task_id: str) -> None    # subscribe to step_finished / task_state for this task
    async def close(self) -> None
```
- **Per step event,** the narrator buffers it. When `min_gap_s` has passed and the user isn't speaking, it asks the `chat` role (purpose `narration`, `max_tokens` 40) for **one** in-character sentence. The prompt has the persona's speaking style, the goal, and the step summaries since the last line. The sentence is spoken through the session's TTS queue as a low-priority sentence: it's dropped if a reply turn is in progress.
- **`task_state` terminal:** the narrator always speaks the task's own summary (no LLM).
- Narration lines aren't saved as messages, because the task's final message is.
- The setting is `voice.narration`.

- [ ] **Tests:**
  - The rate limit (fake clock): 5 steps in 3 s → 1 line.
  - Dropped while a reply is speaking.
  - The terminal summary is spoken verbatim.
  - Off → nothing.
  - The `llm_calls` purpose is `narration`; add it to `usage.PURPOSES` and to `PURPOSE_LABEL` ("Narrating tasks").
- [ ] Implement, then **commit** `feat(voice): narrate tasks during a call`.

### Task B6: The E3 harness
- **`scripts/make_voice_fixtures.py`:** generates 40 utterances with Kokoro, so no recordings are needed. Each is saved as 16 kHz PCM16 WAV under `backend/aethel/eval/voice/` with a `fixtures.json` manifest.
  - **20 complete:** a single segment.
  - **20 mid-thought:** two segments ("so I was thinking that" + 700–1100 ms of silence + "we could go to the coast"), with the gap position recorded.
- **`scripts/eval_voice.py`:**
  1. It plays each fixture into `/ws/voice` in real time, with the client side simulated: it sends `speech_start`/`speech_end` at the known segment boundaries.
  2. It runs against the **running backend** (`--url`, `--token` read from the app's session file if Phase 6 adds one, otherwise given), with `--llm fake|real`.
  3. **Conditions:**
     - **VAD-only:** `eot_threshold = 1.01` (never sure), so it always waits the maximum.
     - **VAD + System 1:** the measured threshold.
  4. **Metrics:** p50/p95 end-of-speech → first audio frame (wall clock at the client), and the **false cut-off rate** (a reply started before the second segment of a mid-thought fixture).
  5. It writes `~/.aethel/eval/voice.json`.
- [ ] Test the metric functions on synthetic timelines. Run it with the fake LLM (no tokens spent) and record the results in the PR. The real LLM run is only with the user's go-ahead. **Commit** `feat(eval): E3 voice latency and cut-off harness`.

### Task B7: Settings → Voice, verify, demo, PR 4b
- **Settings → Voice:**
  - Speech recognition: local (model size) or Groq.
  - "Wait a moment before replying" (the max wait slider, 800–2500 ms).
  - Acknowledgements on/off, narration on/off.
  - Default voice (a blend picker + preview).
  - Last-call latency.
- [ ] Suites → exit 0. Review subagent, then fixes.
- [ ] **Manual demo** (the user):
  1. Start a call and have a short conversation. Mid-thought pauses don't get cut off.
  2. Interrupt mid-sentence: it stops at once, and the saved reply ends where it was cut.
  3. "Open Notepad and write a haiku": narrated, and the task strip is visible.
  4. End the call: the transcript is in the conversation.
- [ ] Open the PR "Phase 4b: call view" and bind it. Update the memory file.

---

## Self-review
- **Spec §8.1 coverage:**
  - Capture → B1, B2.
  - VAD → B2.
  - WS → A6.
  - STT → A1.
  - Turn-taking → A4, A7.
  - Sentence TTS → A2, A3, A6.
  - Playback and visuals → B2, B4.
  - Ack → A6.
  - Barge-in with truncation → A6, B3.
  - Tasks in calls and narration → A6, B5.
  - Transcript → A5, A6.
- **§8.2** (voice blends) → A2. The blend editor is Phase 5, using A8's preview endpoint.
- **§13 voice tests:** the WAV fixtures + `/ws/voice` + a fake LLM → B6.
- **§14 E3** → B6.
- **Names used across tasks:** `VoiceBlend`, `Speech`, `TTS.synthesize`, `SpeechToText.transcribe`, `TurnManager.next_turn`, `run_turn(sink=)`, `Playback.lastStartedSentenceId`, `interrupt.last_started_sentence_id` and `voice_turns`.
