# Aethel — Local-First AI Agent

Aethel is a desktop AI agent that runs **100% on your own machine**. It chats,
speaks, listens, and executes real tasks on your PC under strict permissions —
and it *learns*: every completed task can become a reviewable markdown skill it
uses to do better next time (**RSM — Reflective Skill Memory**).

Nothing leaves your computer unless you explicitly switch on the optional cloud
backend.

## Running Aethel v2

**Desktop app:** `powershell -ExecutionPolicy Bypass -File scripts\start.ps1`
The Tauri shell starts the backend automatically (logs: `~/.aethel/logs/backend.log`).
Add an API key in **Settings → Providers** (Groq is the default conversation model).

**Browser development:**
1. `scripts\start.ps1 -BackendOnly` (API on http://127.0.0.1:8765, auth disabled)
2. set a key for the backend process, e.g. `$env:GROQ_API_KEY = "..."` before step 1
3. `cd frontend_app; pnpm dev` and open http://localhost:5173

**Private mode:** put a `.gguf` model in `models/llm/` (or choose one in Settings → Local model) and install
llama.cpp (`scripts/install.ps1`). Everything then runs on this computer.

**Tasks:** press the **Task** pill in the prompt box and describe a goal (e.g. "write a haiku about rain to
Documents\Aethel\haiku.txt"). Aethel plans a checklist, works through it with file and command tools, and checks the
result before reporting back. Anything outside your allowed folders or commands waits for your approval in the task
panel. Allowed folders and commands live in `~/.aethel/permissions.yaml`. **Undo file changes** restores every
file a finished task wrote.

**Computer control:** tasks can also drive any Windows app through
[Windows-MCP](https://github.com/CursorTouch/Windows-MCP) (needs [uv](https://docs.astral.sh/uv/)), Word, Excel
and PowerPoint through an in-house COM server, and precise file edits, PDF/Excel reading and search through
[Desktop Commander](https://github.com/wonderwhy-er/DesktopCommanderMCP) (needs Node). Each connects in the background
at launch; **Settings → Computer control** shows which are up. Only vetted tools are exposed: their shell, registry
and process tools are never offered, since they would bypass `permissions.yaml`. Actions are scoped to the app they
land in; anything that sends, buys, deletes or closes always asks, and Aethel never operates its own window or sign-in
prompts. **Ctrl+Alt+Esc** stops every task at once, from any app. Set `AETHEL_MCP=0` to turn computer control off.

**Learning:** after each task Aethel keeps notes on the apps it used and writes down how it did the job as a
*skill* (`~/.aethel/knowledge/{apps,skills}/*.md`: plain Markdown you can read and edit). The next similar task
starts from that skill, and skills are credited or retired by whether their tasks actually passed their checks.
**Memory** (in the rail) shows every skill with its runs, success rate and a duration sparkline, and lets you edit
app notes. Small, fast judgments (does this message ask for a task? which skill fits? does this file match the
goal?) come from **System 1**: [Laya](https://huggingface.co/convaiinnovations/laya), a local decision model running
on the CPU (about 0.2 s a judgment). It downloads once (~1.7 GB, to `~/.aethel/models/laya`) and never blocks
anything: until it's ready, Aethel behaves as before. With it, a message that clearly asks for something to be done
starts a task on its own, and "stop" stops one (**Settings → Learning**). The thresholds come from labelled sets
you can extend and re-measure: `py -3.11 scripts/eval_s1_intent.py`, `py -3.11 scripts/eval_s1_judge.py`.

**Macros, cursor and replay:** once a skill has worked **3 times the same way**, Aethel compiles it into a *macro*,
a replayable list of steps stored in the skill file. The next matching task ("play jazz on YouTube in Firefox" after
three "play … on YouTube" runs) replays it without the language model: each step finds its button or field again on
the live screen by name (System 1 picks when the name changed), and if the screen doesn't match, the model takes over
from there and the macro is repaired afterwards. Replayed steps still go through every permission check and approval.
While a task works on your screen, a soft **ghost cursor** shows where it's about to click (it never takes clicks
itself; Ctrl+Alt+Esc still stops everything). **Tasks** in the rail replays any past task step by step, with a small
picture of the screen after each on-screen step (Settings → Learning → Replay pictures), and **Export** saves a task as
JSON + pictures. Skill selection is measured too: `py -3.11 scripts/eval_s1_skill.py`.

**Tests:** `cd backend; py -3.11 -m pytest` · `cd frontend_app; pnpm test` · `cd frontend_app/src-tauri; cargo test`

**Changing the WebSocket protocol:** edit `backend/aethel/protocol.py`, then run
`py -3.11 scripts/gen_event_schema.py` and `cd frontend_app; pnpm gen:types`.

## Highlights

- **Local everything** — llama.cpp for chat, Kokoro for speech, Whisper for
  transcription, turbovec + LanceDB for memory. No account, no API key, no
  telemetry.
- **Learning without fine-tuning (RSM)** — after each conversation and task the
  agent reflects and writes behavioural *rules* and procedural *skills* as plain
  markdown files. You approve or reject each one. Outcome tracking promotes what
  works, retires what fails, and merges duplicates automatically.
- **Real agent loop** — multi-step tool use (files, shell, web search) with a
  permission manifest, a full audit log, and one-click rollback of any file
  change the agent makes.
- **Voice both ways** — speak to it (local Whisper), have it speak back (local
  Kokoro). Models download themselves on first run.
- **Ambient tone** — an optional two-dimensional tone model (mood × energy)
  paints a soft colour around the composer so you can feel how the conversation
  is going without reading a status badge.
- **Measurable** — a built-in A/B benchmark runs the same task suite with and
  without RSM knowledge and reports the difference (`backend/benchmark_agent.py`).

## Install

Prerequisites: [Python 3.10+](https://python.org), [Node.js 20+](https://nodejs.org),
[Rust](https://rustup.rs) (for the Tauri desktop shell).

```bash
# Windows (PowerShell) — add -Cuda for NVIDIA builds of llama.cpp
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

```bash
# Linux / macOS — add --cuda for NVIDIA builds of llama.cpp
bash scripts/install.sh
```

The installer creates a Python environment, installs dependencies, and downloads
the llama.cpp server binary into `bin/llama/`.

## Add a model

Drop a chat model into `models/llm/` — that is the only required manual step.
See [models/README.md](models/README.md) for recommendations and links.

| Folder | What | Required? |
|---|---|---|
| `models/llm/` | Chat model (`.gguf`) | **Yes** |
| `models/tts/` | Kokoro voice | Downloads itself on first run |
| `models/stt/` | Whisper | Downloads itself on first run |
| `models/image/` | Image checkpoint | Optional, and off by default |

You can also point at models anywhere on disk from **Settings → Models** instead
of copying them into the project.

## Run

```bash
# Windows
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

```bash
# Linux / macOS
bash scripts/start.sh
```

This starts the backend — which spawns the LLM engine itself, no manual
llama.cpp wrangling — and opens the desktop app. The first launch loads models
and fetches the speech models in the background, so give it a moment.

Add `-BackendOnly` (or `--backend-only`) to run headless. API docs are at
`http://localhost:8000/docs`.

## How it works

```
frontend_app/     Tauri v2 + React desktop app
backend/          FastAPI server
  agent_loop.py         multi-step tool loop (structured JSON tool calls)
  knowledge_store.py    RSM: rules + skills as markdown on disk
  rule_engine.py        conversation reflection -> behavioural rules
  skill_engine.py       task reflection -> procedural skills
  mcp_executor.py       file/shell/web tools behind the permission manifest
  transaction_log.py    audit log + rollback
  memory.py             episodic memory (turbovec, 4-bit quantised)
  emotion_engine.py     optional conversation tone (mood x energy)
  tts_engine.py         Kokoro (local) with Edge fallback
  stt_engine.py         faster-whisper voice input
  benchmark_agent.py    RSM on/off A/B evaluation
models/           your model files
scripts/          install + start scripts
```

Everything the agent learns lives in `~/.aethel/knowledge/` as markdown you can
read, edit, delete, or put under version control. Permissions live in
`~/.aethel/permissions.yaml`; the rollback log in `~/.aethel/transactions.json`.

### RSM in one paragraph

Fine-tuning a model to make it better at your tasks is slow, expensive, and
opaque. Aethel instead writes what it learns into markdown and feeds the
relevant pieces back through the prompt. A *rule* is a short behavioural
instruction ("don't pad answers with pleasantries"); a *skill* is a procedure
("to move a file: read it, write it to the destination, verify, then delete the
source"). Each document carries outcome statistics, so retrieval is weighted by
what has actually worked, and knowledge that keeps failing is retired
automatically. Because it is all text, you can audit every single thing the
system has "learned".

## Configuration

Copy `.env.example` to `.env` (the installer does this) and edit as needed.

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_LOOP_ENABLED` | `true` | multi-step agent loop |
| `AGENT_MAX_STEPS` | `8` | tool-step budget per turn |
| `GENERATION_BACKEND` | `local` | `local` (llama.cpp) or `cloud` |
| `TTS_ENGINE` | `kokoro` | `kokoro` (local) or `edge` (cloud) |
| `STT_MODEL_SIZE` | `small` | whisper size: tiny/base/small/medium |
| `EMOTION_ENGINE_ENABLED` | `true` | conversation tone tracking |
| `IMAGE_GEN_ENABLED` | `false` | image generation (needs spare VRAM) |
| `MAX_VRAM_ALLOCATION` | `4` | GB budget; ≤4 keeps aux models on CPU |

Most of these are also editable in-app under **Settings**, where each control
explains itself on hover.

## Evaluation

```bash
# A/B benchmark: the same tasks, with and without RSM knowledge
.venv/Scripts/python backend/benchmark_agent.py --seed --runs 3
```

Metrics are also exposed at `GET /admin/eval/metrics`. Every value there is
measured from stored data — where there isn't enough data to compute something,
it reports `null` rather than inventing a number.
