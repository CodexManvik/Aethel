# Aethel v2: Design Spec

- **Date:** 2026-09-23
- **Branch:** `revamp`
- **Status:** approved in brainstorming; awaiting review of this written spec
- **Author:** Manvik Talwar, with Claude

---

## 1. Summary

Aethel is a local-first desktop AI companion. It has a persistent persona, talks with you by voice, sends images of itself, and **acts as an assistant on your Windows PC**. Examples: "read the essay question in `question.docx` and write the essay in Word", or "open Firefox and play some lofi on YouTube". It **learns how to do tasks faster over time**. Every successful task leaves behind app notes, skills and compiled macros, stored as Markdown files, so a task that took 60 s the first time takes about 5 s later.

v2 is a restructuring of the existing `revamp` codebase (FastAPI + Tauri v2/React), not a greenfield project. The parts that already work are kept and rewired: turbovec episodic memory, the RSM knowledge store, the permission manifest, the transaction log, Kokoro TTS, faster-whisper STT, Z-Image, and persona distillation.

### 1.1 Goals
1. A general computer-use agent that can operate **any** Windows application, not only Office.
2. **Measurable procedural learning (RSM):** repeated tasks get faster, cheaper and more reliable, with evidence.
3. A **dual-process agent:** a fast System 1 model (SemIf / Jev) makes narrow decisions; an LLM (System 2) plans and recovers.
4. Hands-free **voice calls** with about 1 s to first audio and barge-in.
5. **Persona-consistent images** sent in chat within a few seconds.
6. Three-layer memory: semantic (Mem0), episodic (turbovec), procedural (RSM).
7. Optional internet access with cited sources.
8. A beautiful, calm **"Paper & Ink"** UI with physical motion.
9. A dissertation-grade evaluation suite.

### 1.2 Non-goals
- Live integration with messaging apps. Persona distillation uses **chat exports only**, as today.
- Model fine-tuning. DPO is gone; learning happens through memory, not weights.
- A wake word or always-listening background mode.
- Driving Firefox page contents through a remote protocol. Firefox is driven through the universal UI layer.
- A live image canvas or conversational image editing.
- Mobile or web deployment. This is a Windows desktop app. The code should not needlessly block macOS/Linux, but they are not targets.

### 1.3 Decisions log (from brainstorming)

| Topic | Decision |
|---|---|
| Compute | **Hybrid.** A cloud LLM drives the agent. Voice, memory and skills stay local. **Private mode** routes every role to local models. |
| Providers | **Groq, Google Gemini, OpenRouter** (plus any OpenAI-compatible endpoint and local llama.cpp). No vendor-native computer-use tool. |
| App control | **Structured-first, UI fallback.** Universal accessibility-tree layer for every app; accelerators (Office COM, Chromium CDP) only where available. |
| Images | In-chat images and persona consistency. Default backend is the Gemini image model; local Z-Image is the offline fallback. |
| Voice | Hands-free call mode with semantic end-of-turn and barge-in. |
| Memory | Mem0 (semantic) + turbovec (episodic) + RSM `.md` (procedural). Skills **auto-approve by default**, with manual approval as an option. |
| System 1 | SemIf (open-source Jev-compatible, formerly OpenJev) locally; hosted Jev optional. |
| Architecture | **Approach A:** restructure into a proper agent runtime, reusing existing parts, with selective library adoption (LiteLLM, MCP SDK, mem0ai, Windows-MCP, vad-web). |
| UI | Fresh identity: **Paper & Ink** (warm editorial light mode, warm-charcoal dark mode). |
| Internet | Optional, toggled globally and per conversation, DuckDuckGo-based, citations as footnotes. |
| Deadline | None. Build the best version, in phases. |

---

## 2. Architecture

```
┌──────────────────────── Tauri v2 shell (Windows) ────────────────────────┐
│  React app (Paper & Ink)            Ghost-cursor overlay window           │
│  Zustand stores ← typed WS events   Global kill-switch hotkey (Ctrl+Alt+Esc)│
└───────────────┬──────────────────────────────────────────────────────────┘
                │  REST (TanStack Query)  +  WS /ws/session  +  WS /ws/voice
┌───────────────▼──────────────── backend/aethel (FastAPI) ────────────────┐
│ api/        routers, WS event bus, typed event models (→ TS codegen)      │
│ runtime/    task engine: plan → act → observe → verify → reflect          │
│ system1/    SemIf / Jev client: Choice, Noul, Score                       │
│ providers/  LiteLLM role router + failover + private mode                 │
│ tools/      MCP hub · universal Windows layer · accelerators · web · fs   │
│ memory/     mem0 (semantic) · turbovec (episodic) · rsm (procedural)      │
│ context/    budgeted context builder                                      │
│ voice/      STT · turn-taking · sentence-streamed TTS · barge-in          │
│ images/     backends · prompt composer · identity check · gallery         │
│ personas/   persona folders · distillation · accent extraction            │
│ safety/     permission manifest · risk tiers · approvals · transaction log│
│ store/      SQLite (conversations, messages, tasks, steps, events)        │
│ eval/       benchmark suites, metrics export                              │
└──────────────────────────────────────────────────────────────────────────┘
      │ MCP stdio                 │ HTTP                    │ subprocess
  Windows-MCP, Playwright-MCP,   SemIf server (local)     llama-server (local LLM)
  aethel-office MCP (in-house)   or TypeSafe API
```

### 2.1 Package layout
`backend/aethel/` is a Python package. The existing flat modules migrate into it:
- `agent_loop.py` → `runtime/`
- `knowledge_store.py` and `skill_engine.py` → `memory/rsm/`
- `permission_manifest.py` and `transaction_log.py` → `safety/`
- `tts_engine.py` and `stt_engine.py` → `voice/`
- `image_generator.py` and `gguf_text_encoder.py` → `images/local/`
- `persona_distillation.py` → `personas/`
- `generation.py` → `providers/local_llama.py`
- `memory.py`, `embeddings.py` and `reranker.py` → `memory/episodic/`

`server.py` (2,056 lines) is dismantled into `api/` routers. Legacy modules with no v2 role are deleted:
- `graph_memory.py`
- the old `[CALL_TOOL:]` regex path
- `[TRIGGER_SELFIE]` / `[SYSTEM_MEDIA_*]` text markers
- the age-gate and access-request flow
- `emotion_engine.py`'s client shim. The tone engine itself stays, see §9.

Each module exposes a small interface and hides its internals. Modules depend on interfaces (`LLMProvider`, `ToolHost`, `System1`, `ImageBackend`, `MemoryLayer`), which makes fakes trivial in tests.

### 2.2 Runtime data
All user data lives under `~/.aethel/`:
```
~/.aethel/
  aethel.db                 SQLite: conversations, messages, tasks, steps, events, settings
  personas/<id>/            persona.md, portraits/, gallery/
  knowledge/
    apps/<app>.md           app notes (procedural tier 1)
    skills/<slug>.md        skills + optional compiled macro (tiers 2–3)
    rules/<id>.md           behaviour rules (existing RSM rules)
  mem0/                     embedded Qdrant store for semantic memory
  episodic/                 turbovec index + metadata
  media/                    generated images, task screenshots
  permissions.yaml          existing manifest, extended with risk tiers
  transactions.json         existing rollback log
```
Existing data under `~/.aethel/` (skills, permissions, transactions) is migrated in place. The one-time migration is idempotent and guarded by a sentinel file.

---

## 3. Providers and model roles

- **LiteLLM** is the single client for Groq, Gemini, OpenRouter, any OpenAI-compatible endpoint, and the local llama.cpp server (`generation.py`'s process lifecycle, including suspend/resume for VRAM handoff, is kept).
- **Roles**, each mapped to a model in Settings:

| Role | Purpose | Default (hybrid) | Private mode |
|---|---|---|---|
| `chat` | conversation, narration, memory extraction | Groq fast model | local llama.cpp |
| `agent` | planning, tool use, recovery, long writing | Gemini (Pro/Flash) or OpenRouter | local llama.cpp |
| `vision` | screenshot grounding fallback | Gemini Flash | local VLM if configured, else disabled |
| `system1` | narrow judgments | SemIf Qwen3.5-4B (GPU) | SemIf MiniCPM5-2B (CPU) |

- **Failover chain:** each role has an ordered list of `provider:model` entries. On a rate-limit error, a 5xx or a timeout, the router moves to the next entry and emits a `provider_switched` event, which the UI shows as a small toast.
- **Private mode:** one switch. It forces every role to local models, disables `web.*` tools and cloud image backends, and shows a visible badge.
- **API keys** are stored in the Windows Credential Manager through the Rust `keyring` crate, exposed as Tauri commands, never in `settings.json`. At startup the shell pushes them to the backend over the authenticated local API (`POST /api/secrets`), and the backend keeps them in memory only. The existing plain-text `cloud_api_key` setting is migrated, then removed.
- **Tool calling** uses native function calling for every provider. The fenced-JSON protocol stays **only** as a fallback for local models without function-calling support, with `ToolCallStreamFilter` kept.

---

## 4. Agent runtime (System 2)

### 4.1 Task lifecycle
A **task** is created when intent routing (§5) classifies a message as a task, or when the user presses the Task pill. States:

`planning → running ⇄ waiting_approval ⇄ paused(take-over) → verifying → reflecting → done | failed | cancelled`

- **Plan:** the `agent` model writes a checklist (3–10 steps) and a **verification spec**: postconditions expressed as checkable facts, e.g. `file_exists(~/Documents/essay.docx)`, `word_count(doc) >= 1000`, `window_title_contains("YouTube")`, `media_playing()`. Checklist items stream to the UI.
- **Act and observe:** a native function-calling loop. Read-only tool calls in one turn may run in parallel. Each step is persisted as a `steps` row with inputs, outputs, duration, the decider (S1 or S2), a screenshot thumbnail when relevant, and token cost.
- **Budgets:** a max step count (default 40), a max wall time (default 15 min), and a max cost per task (configurable). Hitting a budget triggers a final summary turn with tools disabled, as in today's `[STEP LIMIT REACHED]`.
- **Loop guards:** the existing identical-call and per-tool caps are kept and made configurable.
- **Verify:** postconditions are checked by code (and by System 1 Noul for fuzzy ones such as "the essay addresses the question"). A task only reaches `done` if verification passes. Otherwise the agent gets **one repair attempt** with the failed checks as feedback, then `failed`.
- **Reflect:** described in §6.3.
- **Durability:** task state checkpoints to SQLite after every step. On restart, interrupted tasks show as `paused`, with Resume and Cancel.
- **Take-over:** pauses the loop. The user acts manually and presses Resume. The agent re-observes before continuing.
- **Chat during a task:** the conversation stays usable. Messages sent while a task runs go to the `chat` role with the task status in context. "Stop" or "cancel that" is routed (§5) to task control.

### 4.2 Tools: MCP hub
- `tools/hub.py` connects to MCP servers over stdio using the official `mcp` Python SDK. It merges their tool lists into one registry, tagged with `server`, `risk_tier` and `app` metadata.

**Servers:**

| Server | Source | Provides |
|---|---|---|
| `windows` | [Windows-MCP](https://github.com/CursorTouch/Windows-MCP) (pinned version; forked into `mcp_servers/` if we need changes) | app launch, window list/focus/resize, accessibility-tree snapshot, click/type/scroll/shortcut by element, screenshot, clipboard |
| `browser` | Playwright MCP | DOM-level control of Chromium browsers (Edge/Chrome) through CDP, attaching to the user's browser when it's launched with remote debugging, otherwise a managed profile |
| `office` | in-house `aethel_office` (Python, pywin32 COM) | Word: open/create/read/insert text as visible typing, styles/headings/save. Excel: read/write ranges. PowerPoint: add slides/text. Always `Visible=True`. |
| `local` | in-process (not MCP) | `fs.*`, `shell` (existing, permission-gated), `web.search`, `web.read`, `image.generate`, `memory.remember/recall`, `ask_user` |

**The universal layer** (built on the `windows` server plus in-house helpers) works on every app:
1. **Launcher:** an index of Start-menu shortcuts, App Paths registry entries and UWP packages, with fuzzy matching. It also opens URIs and protocols (`https://…` in the default browser, `ms-settings:`, `spotify:`…).
2. **Window manager:** list, focus, snap, minimise, and `wait_for_window(predicate)`.
3. **Accessibility snapshot:** interactive, visible elements only, with name, role, automation ID and bounds. It's filtered and ranked before any model sees it (§5.3).
4. **Vision fallback:** when the tree is empty or unhelpful, a screenshot plus a candidate grid goes to the `vision` role, which returns a target.
5. **Raw input:** keys, shortcuts, clicks at coordinates, as a last resort.

**Accelerator selection:** a tool with a structured path (e.g. `office.word.insert_text`) is preferred when it's available and the target app matches. The planner prompt and app notes steer this; it isn't hard-coded.

### 4.3 Safety
- **Risk tiers** on every tool: `read` (auto), `write` (auto within manifest-allowed scopes, otherwise ask), `irreversible` (always ask: send, submit, purchase, delete outside the rollback log, closing unsaved documents, installing software). The tier is declared by the tool, and `permissions.yaml` can raise it per app or per path, never lower it for `irreversible`.
- **Approvals:** a `approval_needed` event shows an inline card (the action, the target, a screenshot, Allow once / Always for this app / Deny). The task sits in `waiting_approval`.
- **Kill switch:** the global hotkey `Ctrl+Alt+Esc` (Tauri global-shortcut plugin) immediately cancels the running task and any pending input injection. This is also a button in the ghost-cursor overlay.
- **Untrusted content:** text read from web pages, documents or other apps goes into model context inside explicit `<untrusted source="…">` wrappers, together with a standing instruction that such text is data. Actions whose arguments trace back to untrusted content and are `write` or `irreversible` require approval.
- **Rollback:** existing file transaction logging applies to `fs.*` and `office.*` saves.
- **Hardening:** the existing shell hardening (`forbidden_arguments`, no interpreter `-c`) is kept.

### 4.4 Ghost cursor overlay
A second Tauri window: transparent, always on top, click-through, covering the active monitor. The backend emits `cursor_intent {x, y, label}` before every pointer action. The overlay animates a soft cursor in the persona's accent colour along a spring path, with a caption ("Opening Word…"). It hides when no task is running. It carries the kill-switch button, which is the only part that accepts clicks.

### 4.5 Replay timeline
Every step stores a thumbnail (JPEG, ≤ 480 px wide) when the step touched the screen. The Task history screen shows a scrubbable timeline: step list, thumbnail, tool call, decider, and duration. Tasks can be exported as a JSON + images bundle for the dissertation appendix.

---

## 5. System 1 (fast judgments)

### 5.1 Backend
- The client is `typesafe-sdk` (Python), pointed at either:
  - **SemIf** running locally (FastAPI server exposing `/v1/systemone`, launched and supervised by Aethel like llama-server). Default model: **Qwen3.5-4B** on GPU in hybrid mode; **MiniCPM5-2B** on CPU in private mode. Published agreement with TypeSafe on their 102-row subset: 84.5% and 63.7% respectively.
  - **Jev** through the TypeSafe API (optional, key in the credential store).
- **Verification task (Phase 2, first step):** establish which repository is canonical (`TheoLeeCJ/SemIf` vs `GitHub30/OpenJev`), pin a commit, and confirm SDK compatibility with a smoke test before building on it.
- `system1/` wraps the SDK behind a `System1` interface: `choice(state, question, options) -> (answer, probs, confidence)`, `noul(state, question) -> p_yes`, `score(state, question, levels) -> (value, probs)`. Every call is logged with its inputs, outputs and latency, for evaluation.

### 5.2 Uses

| Decision | Primitive | Consumer |
|---|---|---|
| Intent routing: chat / task / image / task-control / voice-control | Choice | api (every user message) |
| Element grounding: which candidate element matches the step's target | Choice (+ "none of these") | universal layer |
| State checks: app ready, page loaded, media playing, dialog open | Noul (polled) | runtime waits, verification |
| Window targeting | Choice | universal layer |
| Skill selection: which learned skill fits this task, or none | Choice (two-stage ranking over the top-k by embedding) | runtime planning |
| Macro drift: does the current UI match what this macro step expects? | Noul | macro executor |
| Semantic end-of-turn: has the user finished their thought? | Noul | voice |
| Fuzzy postconditions | Noul | verification |

### 5.3 Confidence routing
- Each use has a threshold (initial values from a calibration run, stored in settings, then tuned on our own labelled data).
- **Above the threshold:** code acts directly, with no LLM call.
- **Below:** the decision escalates to System 2 with System 1's top-3 candidates and their probabilities included.
- **Input hygiene:** code prefilters state before any System 1 call. Only visible and interactive elements are kept, the top-N (default 25) by embedding similarity to the target description, with short normalised labels. The Jev limitations page documents degradation with irrelevant context and multi-hop questions, so every question is kept narrow and single-hop.

---

## 6. Memory

### 6.1 Semantic: Mem0
- `mem0ai` open-source library. Embedder: local bge-small (existing `embeddings.py` singleton). Vector store: embedded Qdrant at `~/.aethel/mem0/`. Extraction LLM: the `chat` role (local in private mode). Graph mode is off.
- Extraction runs **asynchronously** after each user turn, so it never blocks replies.
- **Scopes:** `user_id="me"` for facts about the user, shared across personas. `agent_id=<persona>` for relationship memories.
- **UI:** the Memory screen lists facts as index cards. Each one can be edited or deleted, and you can see where a fact came from (a link to its message).

### 6.2 Episodic: turbovec
The existing `memory.py` index covers **Aethel's own conversation history** (messages from SQLite, chunked by exchange). It's rebuilt incrementally, and imported persona corpora are indexed separately. Recall is "remember when…" style, with the existing reranker.

### 6.3 Procedural: RSM (the dissertation core)
Three tiers, all Markdown with YAML frontmatter. The files on disk are the source of truth; the vector index is a derived cache, as in the existing `knowledge_store`.

**Tier 1, app notes (`knowledge/apps/<app>.md`).** Facts about one application: launch method, readiness signal, useful shortcuts, element names, pitfalls, typical timings. Written and updated automatically after tasks; **always auto-approved** (they're observations, not policies).

**Tier 2, skills (`knowledge/skills/<slug>.md`):**
```yaml
---
id: play-youtube-video
type: skill
apps: [firefox]
intent: "Play a video on YouTube matching a search query"
params: {query: string}
preconditions: ["firefox installed"]
status: approved          # quarantined | approved | deprecated
runs: 7
successes: 6
avg_duration_s: 3.4
duration_history: [48.1, 12.0, 4.1, 3.6, 3.2, 3.3, 3.4]
confidence: 0.78          # Laplace-smoothed success rate × reinforcement
last_used: 2026-09-23
macro: compiled           # none | compiled | broken
---
## Steps
1. Launch Firefox (see apps/firefox.md).
...
## Pitfalls
- Cookie consent dialog on first visit — click "Reject all".
```

**Tier 3, macros:** a fenced `macro` YAML block inside a mature skill. It's a parameterised sequence of tool calls. Elements are addressed by `{app, window_pred, role, name|automation_id}`, never by raw coordinates. Coordinates are allowed only for steps that originally needed the vision fallback, and only as relative positions inside a named window.

**Learning loop:**
1. **Reflect** (after verify):
   - The `agent` model receives the step log and the verification outcome, and proposes updates: new or changed app notes, a new skill, or amendments to the skill it used.
   - Duplicates are found with the existing `find_duplicate` + `reinforce` logic and merged.
   - Status: skills go straight to `approved` when auto-approve is on (the default); otherwise they're `quarantined` until you approve them in the UI.
2. **Outcome attribution:** skills and notes retrieved for a task are credited or debited according to the **verification result**, not "no tool errored". A thumbs-down in chat also debits them (existing behaviour). Skills with a success rate below 34% after at least 3 runs are auto-deprecated (existing rule).
3. **Compilation:**
   - After **3 consecutive verified successes** with the same step structure, the runtime compiles the skill's recorded step sequence into a macro, lifting literal values that match task parameters into `{params}`.
   - Compilation is deterministic code, not an LLM call, and the macro is written into the skill file.
4. **Execution:**
   - When skill selection (System 1) picks a skill with a compiled macro, the runtime executes the macro step by step. Before each step, a System 1 drift check (Noul) and element grounding run on the live accessibility tree.
   - Any drift or failure hands control to System 2 **mid-task**, with the macro progress so far as context.
   - After that recovery, the macro is **repaired**: the diverging steps are replaced with the new successful sequence. If repair fails twice, the macro is marked `broken` and the skill runs LLM-guided.
5. **Timing:** every run appends to `duration_history`. This is the data behind the "getting faster" sparklines and the learning-curve experiment.

**Behaviour rules** (existing RSM `rules/`) remain the channel for "how to act" preferences learned from feedback. Mem0 holds facts, rules hold behaviour, skills hold procedures.

### 6.4 Context builder
`context/builder.py` assembles each model call from a declarative recipe per role:
- persona card and style directive
- behaviour rules
- Mem0 facts (top-k)
- episodic recall (top-k after reranking)
- app notes for the apps involved
- selected skills
- task state
- conversation window

It enforces a **token budget per model**, read from the provider's context size and capped by a per-role setting. Sections are dropped from lowest to highest priority when over budget. The builder records which document IDs were included; this replaces today's `LAST_RETRIEVED_DOC_IDS` global.

---

## 7. Internet (optional)
- **Toggles:** global in Settings and per conversation (the prompt-box pill). Private mode forces it off.
- **Tools:**
  - `web.search` (the `ddgs` DuckDuckGo package, returning title, snippet and URL)
  - `web.read` (httpx fetch + `trafilatura` main-text extraction, max about 8k tokens, with the URL and title kept)
  - Interactive sites go through the browser/universal layer instead.
- **Citations:** the model is asked to cite with `[n]` markers mapped to sources. The UI renders them as superscript footnotes with a source list under the message.
- All web content is wrapped as untrusted (§4.3).

---

## 8. Voice: call mode

### 8.1 Pipeline
```
mic → getUserMedia(echoCancellation, noiseSuppression) → AudioWorklet (16 kHz PCM16)
    → Silero VAD in-app (@ricky0123/vad-web)  ── speech_start / speech_end
    → WS /ws/voice (binary frames + JSON control)
server: utterance buffer → STT → turn-taking → context builder → LLM (chat role, streamed)
      → sentence chunker → TTS per sentence → WS audio chunks (+ sentence ids)
client: playback queue (AudioWorklet) → speakers; AnalyserNodes feed the call visuals
```
- **STT:** Groq `whisper-large-v3-turbo` in hybrid mode; local faster-whisper (existing) in private mode.
- **Turn-taking:**
  - On VAD `speech_end`, the server transcribes and then asks System 1: *has the user finished their thought?*
  - If `p_yes ≥ τ` (initially 0.6), it replies after 300 ms of silence. Otherwise it waits up to 1.5 s for more speech before replying anyway.
  - New speech within the window is appended to the same utterance.
- **TTS:** Kokoro (existing `tts_engine.py`), streamed sentence by sentence. The first sentence is synthesised as soon as it completes. `edge-tts` stays as the fallback.
- **Latency target:** p50 ≤ 1.2 s from end of speech to first audio in hybrid mode. The optional cached acknowledgement ("mm, okay—") plays only when the LLM's time to first token exceeds 600 ms.
- **Barge-in:**
  - When the VAD detects user speech while audio is playing, the client **immediately** stops and flushes playback and sends `interrupt {last_played_sentence_id}`.
  - The server cancels generation and TTS, and **truncates** the stored assistant message to the sentences actually played.
- **Tasks in calls:** intent routing applies to call utterances too. A task started in a call shows a compact strip in the call view, and the persona narrates progress: step events are summarised into short in-character lines by the `chat` role, rate-limited to at most one line every 8 s.
- **Transcript:** the whole call (both sides, captions) is saved as normal messages in the conversation, with `modality: voice`.

### 8.2 Persona voices
- Each persona stores a **Kokoro voice blend**: a weighted mix of style vectors, e.g. `{af_heart: 0.7, bf_emma: 0.3}`, plus speed.
- The persona studio has a blend editor with a preview.
- Voice cloning is out of scope for v2; the engine interface stays pluggable.

---

## 9. Personas

- **Folder:** `~/.aethel/personas/<id>/`, containing:
  - `persona.md`: frontmatter with `name`, `voice`, `accent` (derived), `image_style`, `anchors`, `sheet`, `image_frequency`; the body has personality, backstory, speaking style and boundaries.
  - `portraits/`
  - `gallery/`
  - the distillation style directive (existing pipeline from chat exports, unchanged in principle)
- **Built-in "Aethel" persona:** neutral assistant style, no portrait requirement. It replaces "plain assistant mode". The legacy hard-coded "Rosia" persona is converted into a persona folder by migration.
- **Creation flow:**
  1. Describe the character.
  2. The LLM drafts `persona.md`, and you edit it.
  3. Four portrait candidates are generated, and you pick the anchor.
  4. A character sheet is generated (¾ view, profile, 2–3 expressions); these are identity-checked (§10.3) and saved as references.
  5. Choose the voice blend.
- **Accent extraction:** k-means on the anchor portrait's pixels picks a dominant non-skin colour. It's clamped in OKLCH (L 0.55–0.68 on light / 0.68–0.78 on dark, C ≤ 0.13) so it always suits the paper palette.
- **Tone engine:** the existing mood/energy tone engine is kept as an optional per-persona feature (it drives the subtle accent-glow behaviour in the UI). It's on by default for companion personas (matching the July decision) and off for the built-in Aethel assistant persona.

---

## 10. Images

### 10.1 Trigger
- `image.generate(scene, framing?, expression?)` is a normal tool available to the `chat` role.
- **Persona setting `image_frequency`:** `never | on_request | sometimes`. With `sometimes`, the prompt permits spontaneous images at most once every ~15 turns (enforced by code).
- The agent can also call it during tasks (e.g. "make a thumbnail").

### 10.2 Composition and backends
- **Prompt:** code composes it as anchor + sheet references + the persona's `image_style` + the scene + a fixed consistency instruction. The LLM writes only the scene.
- **Interface:** `ImageBackend.generate(prompt, reference_images, size) -> bytes`.
  1. **Gemini image model** (default; supports several reference images). **Verification task:** confirm the current model ID and its reference-image limits when implementing.
  2. **OpenRouter** image-capable models.
  3. **Local Z-Image Turbo** (existing code, including VRAM handoff). Text-only consistency, labelled as weaker in the UI. Used in private mode or offline.

### 10.3 Identity check
- Face detection with OpenCV **YuNet** (built into `cv2.FaceDetectorYN`), plus an **ArcFace ONNX** embedding on CPU through onnxruntime.
- Cosine similarity between the new face and the anchor must be ≥ τ_id (initially 0.45, calibrated). If it fails, regenerate once; the better of the two is kept, and the score is stored.
- Images with no detectable face (scenery, objects) skip the check.
- Passing images may be added to the reference pool (at most 6 references in total).

### 10.4 UX
- `image_pending {id, caption}` shows the **developing-photo** card: warm, blurred and grained, resolving over the generation time.
- `image_ready {id, url, identity_score}` swaps in the image with a focus-pull animation.
- `image_failed` shows a quiet inline note and a retry.
- Every image is saved to the persona's gallery ("memories" album).

---

## 11. API and events

- **REST** (`/api/...`) covers CRUD: conversations, personas, memory facts, skills, notes, settings, tasks, permissions, eval results.
- **WebSocket `/ws/session?conversation=<id>`** carries server → client events, all Pydantic models with a `type` discriminator:
  - `message_start`, `token`, `message_end`, `citation`
  - `task_created`, `plan`, `step_started`, `step_finished`, `approval_needed`, `task_state`, `verification`
  - `cursor_intent`
  - `image_pending`, `image_ready`, `image_failed`
  - `provider_switched`, `memory_saved`, `skill_learned`, `error`
- Client → server: `user_message`, `approve`, `deny`, `cancel_task`, `pause_task`, `resume_task`.
- **WebSocket `/ws/voice`** carries binary audio frames plus JSON control (`speech_start`, `speech_end`, `interrupt`, `caption`, `tts_chunk_meta`).
- **Codegen:** `scripts/gen_types.py` exports the event models to JSON Schema and generates `frontend_app/src/lib/events.gen.ts`. CI fails if the generated file is stale.
- **Auth:** the backend binds to `127.0.0.1` only. Every request needs a per-launch random token that Tauri passes to the webview. This replaces the old access-request/approved-token flow.

---

## 12. Frontend: Paper & Ink

### 12.1 Stack
React 18 (upgrade to 19 is optional, not required) + Vite + Tauri v2 + Tailwind v4 + Radix primitives + `motion`. **Zustand** for state (session, tasks, voice, UI), **TanStack Query** for REST, and one WS client feeding the stores. Components are rebuilt from scratch. Unused shadcn scaffolding, the Figma leftovers, `canonical_face_model.obj` and the old dashboards are deleted.

### 12.2 Visual system

| Token | Light | Dark |
|---|---|---|
| canvas | `#f3efe7` | `#1a1815` |
| paper (surface) | `#fbf9f4` | `#211e1a` |
| ink (text) | `#1d1b17` | `#ece6da` |
| muted | `#7a746a` | `#9a9285` |
| hairline | `#e2dccf` | `#2c2823` |
| accent (default terracotta; replaced by the persona accent) | `#c2553a` | `#e07a5f` |

- **Type:** Instrument Serif (display, persona names), Newsreader (the persona's own messages and captions), Inter (interface). Fonts are self-hosted, so the app works offline.
- **Texture:** subtle paper grain (SVG noise overlay, 6% multiply in light mode, a lower-opacity screen blend in dark mode).
- **Rules:** one accent colour per screen. Dark mode is warm charcoal, never pure black. Hairlines over shadows. Shadows only for lifted objects (photos, popovers).

### 12.3 Screens
1. **Conversation:** persona rail, thread, prompt box (pills: Internet / Task / Image; mic; attach). The task panel slides in only while a task is active or pinned.
2. **Call view:** portrait with breathing rings driven by output amplitude, serif captions with the user's words faint beneath, a task strip, and mute / end call / show chat controls. (The approved v3 mockup.)
3. **Persona studio:** editor, a 2×2 portrait candidate grid, the character sheet, the voice-blend editor, the gallery.
4. **Memory:** tabs for Facts (Mem0 index cards), Skills (cards with runs, success and a duration sparkline; approve/deprecate; view the macro), App notes, and Rules.
5. **Tasks:** history list plus the replay timeline.
6. **Settings:**
   - Providers and keys, models per role, failover order
   - Private mode, Internet
   - Permissions (visual editor for `permissions.yaml`)
   - Voice, images
   - Motion, theme
7. **First run:** welcome, then choose hybrid (enter keys) or fully local (model download/check), then meet the Aethel persona or create one.
8. **⌘K command palette:** switch persona, new chat, start call, run a skill, open any screen.
9. **Evaluation dashboard:** benchmark runs, learning curves, S1 accuracy, voice latency, identity scores.

### 12.4 Motion
- Springs everywhere (`motion`). Standard spring: stiffness 260 / damping 30. Gentle spring: 140 / 22.
- **Shared elements:** the persona avatar (rail) → header portrait → call-view portrait, through `layoutId`. Screen changes use cross-fade plus a 6 px rise.
- **Ink settling:** persona tokens render inside spans that animate from opacity 0.35 and blur 1.2 px to opacity 1 and blur 0 over 350 ms. There's no blinking cursor.
- **Task steps:** a check is drawn as an SVG pen stroke (stroke-dashoffset, 280 ms). The active step has a slow terracotta arc spinner.
- **Developing photo:** blur 14 px → 0, sepia 0.4 → 0, grain fading out, over the generation time, then a focus pull when the image is ready.
- **Reduced motion:** the existing `useMotionPreference` hook is kept. All motion tokens collapse to opacity-only fades.

---

## 13. Testing

- **Backend:** `pytest` in `backend/tests/`. The existing ~88 scratchpad checks are ported into it.
  - Fakes: `FakeLLMProvider` (scripted streams and tool calls), `FakeToolHost` / fake MCP server, `FakeSystem1` (scripted probabilities), `FakeImageBackend`.
  - Required coverage: the task state machine, budget and loop guards, approval flow, verification and repair, macro compilation/execution/drift/repair, skill dedup and attribution, context-builder budgeting, failover, private-mode enforcement, untrusted-content wrapping, barge-in truncation, event schema round-trip.
- **Desktop integration:** `@pytest.mark.desktop`, run manually on Windows. Tasks against Notepad, File Explorer, Calculator, a local test HTML page in Firefox and Edge, and Word (when installed).
- **Frontend:** Vitest + Testing Library for stores and components. Playwright end-to-end tests against the Vite build with a mock WS server that replays recorded event streams.
- **Voice:** WAV fixtures (a complete utterance, a mid-thought pause, a barge-in) replayed through `/ws/voice` with a fake LLM, measuring latency and cut-offs.
- **Contract:** a generated-types staleness check.
- A phase is complete when its tests pass and its manual demo scenario (§15) works on the user's machine.

---

## 14. Evaluation (the dissertation)

| # | Experiment | Conditions | Metrics |
|---|---|---|---|
| E1 | Computer-use suite: about 30 tasks across Notepad, Word, Firefox/YouTube, File Explorer, Settings, Calculator, Edge; each with a programmatic checker | (a) LLM only, (b) + System 1, (c) + skills and notes, (d) + macros; 5 repeated runs each | success rate, steps, wall time, LLM tokens, cost, learning curve over repeats |
| E2 | Element grounding: about 300 labelled (tree, instruction → element) cases collected from E1 runs | SemIf-2B, SemIf-4B, Jev (if a key is available), `agent` LLM | top-1 accuracy, calibration (ECE), latency |
| E3 | Voice turn-taking: about 40 scripted utterances with natural pauses | VAD-only vs VAD + semantic end-of-turn | p50/p95 time to first audio, false cut-off rate |
| E4 | Persona identity: 50 scenes | anchor only vs anchor + sheet | ArcFace similarity distribution, regenerate rate |
| E5 | Semantic memory: long multi-session conversations with fact-recall questions | Mem0 off vs on | recall accuracy |

- The existing `benchmark_agent.py` (10 tasks, fs-based) becomes part of E1.
- Results are written to `~/.aethel/eval/*.json` and shown on the evaluation dashboard.
- **Honesty rule** (carried over from the July eval rewrite): no placeholder metrics. Anything unmeasured is shown as "not measured".

---

## 15. Phases

Each phase gets its own implementation plan (writing-plans) and ends with a working app plus a demo scenario.

| Phase | Scope | Demo scenario at the end |
|---|---|---|
| **0: Foundation** | package restructure; SQLite store and migrations; LiteLLM role router + failover + private mode; keychain secrets; typed WS events + codegen; local auth token; new frontend shell with Paper & Ink tokens, fonts, rail, conversation, prompt box, settings (providers/models); delete legacy UI | Chat with the Aethel persona via Groq with streamed ink-settling text; switch to private mode and chat locally |
| **1: Agent** | task engine; MCP hub; Windows-MCP + universal layer (launcher, windows, tree, vision fallback); `aethel_office`; risk tiers, approvals, kill switch; task panel UI | "Open Notepad, write a haiku about rain and save it to Desktop" and the Word essay task, with approval and kill switch working |
| **2: Learning** | SemIf verification and integration; System 1 uses (§5.2); app notes / skills / macros; compilation, execution, drift, repair; ghost cursor; replay timeline; Memory → Skills and Notes UI | Run "play lofi on YouTube in Firefox" 4 times; watch the time drop and the macro compile; the ghost cursor visible |
| **3: Memory and web** | Mem0; episodic re-index on SQLite; context builder; Memory → Facts UI; `web.search`/`web.read` with citations | Tell it a fact, then recall it in a new conversation; ask a current-events question and get footnotes |
| **4: Voice** | `/ws/voice`; VAD, STT, semantic end-of-turn, sentence TTS, barge-in; call view; persona narration during tasks | A hands-free call; interrupt mid-sentence; start a task by voice and hear it narrated |
| **5: Personas and images** | persona folders + migration; studio; portrait and sheet generation; accent extraction; voice blend; image backends; identity check; developing-photo card; gallery | Create a persona end to end, then ask it for a picture at the beach and get a consistent face |
| **6: Polish and evaluation** | first-run onboarding; ⌘K palette; teach mode (record a demonstration into a draft skill); evaluation dashboard; E1–E5 harnesses; README and docs | Full demo run plus the benchmark report |

---

## 16. Risks and mitigations

| Risk | Mitigation |
|---|---|
| SemIf is a small community project; its quality or maintenance could disappear | Keep it behind the `System1` interface; pin a commit; Jev and "LLM as System 1" are drop-in alternatives; E2 measures the real quality |
| Accessibility trees are poor in some apps (games, Electron quirks) | Vision fallback; app notes capture what works for each app |
| Free-tier rate limits (Groq/Gemini) during long tasks | Failover chain; per-task cost/step budgets; local fallback |
| The agent acts on the real desktop | Risk tiers, approvals, kill switch, rollback log, untrusted-content wrapping |
| 4 GB VRAM contention (local LLM + SemIf-4B + Z-Image) | Hybrid default keeps the LLM in the cloud; SemIf runs on CPU in private mode; the existing VRAM handoff for Z-Image |
| Windows-MCP API changes | Pinned version; adapter layer in `tools/universal/`; option to vendor it |
| Scope | Strict phase gates; every phase ships a working app |

## 17. Environment notes
- The user's machine: Windows 11, RTX 3050 Laptop (4 GB), global `py -3.11` (pywin32 available). `models/llm/` is currently empty, so private mode needs a GGUF placed there (documented in first run).
- Working branch: `revamp`. `.superpowers/` is git-ignored.
