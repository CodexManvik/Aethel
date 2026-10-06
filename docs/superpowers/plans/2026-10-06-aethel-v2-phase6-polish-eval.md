# Aethel v2 · Phase 6 (Evaluation and Polish): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Execution:** inline. Two PRs: **6a (evaluation)** on `v2-phase6a`, then **6b (polish)** on `v2-phase6b`.

**Goal:**
- The dissertation's evidence, reproducible from one place. E1–E5 run through a shared harness, store results, and show up on an evaluation dashboard. Everything unmeasured says "not measured".
- Then the polish that makes Aethel feel finished: first-run onboarding, a ⌘K palette, teach mode, a permissions editor, deleting the legacy backend, and docs.

**Architecture:**
- **The eval harness** (`backend/aethel/eval/`):
  - It generalises what `scripts/eval_tokens.py` learned the hard way: an in-process engine, settings snapshot and restore with a crash marker, a refusal to run beside the app, per-task window-allowlisted approvals, workspace reset, and independent result checkers.
  - Experiments are declared as data (tasks × conditions × reps). Results land in SQLite plus JSON, and the dashboard reads them over REST.
- **Polish features** sit on existing pieces:
  - Onboarding writes settings and keys.
  - The palette dispatches existing actions.
  - Teach mode records the user's own clicks and keys (a low-level input hook + Windows-MCP snapshots) into a quarantined draft skill, which only compiles into a macro after 3 verified runs.

**Tech Stack:** Python (the harness, `pynput` for teach mode, new and pinned), the existing Windows-MCP, the `llm_calls` and `voice_turns` tables, React (hand-rolled SVG charts, no chart library), and Radix dialog (palette).

**Spec:** parent spec §12.3 items 7–9 (first run, palette, evaluation dashboard), §13 (desktop integration tests), §14 (E1–E5), §15 Phase 6. Roadmap decisions D8 and D9.

## Global Constraints
- Every earlier constraint still applies (roadmap §6).
- **The honesty rule:** every metric on the dashboard is measured from stored runs or shows "not measured". There are no illustrative numbers.
- **Evals that drive the desktop or spend tokens only run with the user's go-ahead.** While one runs, nobody uses the PC. The harness checks that the app is closed, allowlists approvals per task window and file root, and restores settings even after a kill.
- **Conditions are switched only through settings,** so a condition is fully described by a settings patch, stored with its run.
- **Learning experiments isolate memory:** each E1 condition with learning gets its own fresh knowledge folder (`AETHEL_KNOWLEDGE_DIR` override), so conditions don't leak skills into each other.
- **Teach mode never records passwords:** keystrokes into password fields (UI Automation `IsPassword`) are dropped, and recording is visibly on (an overlay badge) and stops with Esc or the kill switch.
- **Deleting the legacy code** happens only after a grep proves nothing in `backend/aethel/` imports it. Any legacy file that holds data the migrations read (`backend/config.py` for the Rosia prompt) is kept until the migration reads it from a copied constant instead.

---

# Part A: Evaluation (PR 6a)

## File map (Part A)
| File | Change |
|---|---|
| `backend/aethel/eval/harness.py` | `Harness`: guards, settings snapshot/restore, approvals policy, workspace, in-process services |
| `backend/aethel/eval/checks.py` | result checkers (files, window titles, registry reads, clipboard) |
| `backend/aethel/eval/results.py` + migration `017_eval.sql` | `eval_runs`, `eval_items`; JSON export |
| `backend/aethel/eval/e1_tasks.py` | the 30 E1 task definitions |
| `backend/aethel/eval/e1.py`, `e2.py`, `e5.py` | runners (E3 and E4 runners exist from Phases 4 and 5 and are moved under `eval/`) |
| `backend/aethel/settings.py` | `use_macros`, the `AETHEL_KNOWLEDGE_DIR` override |
| `backend/aethel/runtime/engine.py` | records grounding cases (E2) |
| `backend/aethel/api/routes/eval.py` | runs, items, summaries |
| `scripts/eval.py` | one CLI: `py -3.11 scripts/eval.py e1 --conditions a,b,c,d --reps 5 [--tasks …] [--base-url …]` |
| `frontend_app/src/features/eval/*` | the evaluation dashboard |
| `docs/evaluation.md` | how each experiment is run and read |

### Task A1: The shared harness and results store
**Produces:**
```python
@dataclass
class TaskSpec:
    name: str; goal: str; windows: tuple[str, ...]; files_root: Path | None
    setup: Callable[[Path], None] | None; check: Callable[[Path], bool | None]   # None = only the task's own verdict
    app: str; needs: tuple[str, ...] = ()        # e.g. ("office",): skipped ("not run") if unavailable (D8)
@dataclass
class Condition:
    name: str; settings_patch: dict; fresh_knowledge: bool = False
class Harness:
    def __init__(self, *, base_url: str | None, model: str | None, context: int, timeout_s: int): ...
    async def __aenter__(self) -> "Harness"      # refuse if the app answers on :8765; build services; write the marker;
                                                 # start MCP; wait for windows (and office/files if any task needs them)
    async def __aexit__(self, *exc)              # restore settings from the marker; stop MCP; close
    async def run_task(self, spec: TaskSpec, condition: Condition) -> dict
        # reset the workspace (spec.setup), apply the condition, start the task, answer approvals with
        # decide(spec, …) (allowlisted windows + files_root, never irreversible), wait (timeout), check,
        # collect usage (llm_calls), steps, wall time, the macro/LLM step split, the skill used
def decide(spec: TaskSpec, tool: str, summary: str, tier: str) -> Literal["allow_once", "deny"]
class Results:
    def start_run(self, experiment: str, config: dict) -> str
    def add_item(self, run_id: str, item: dict) -> None
    def finish_run(self, run_id: str, summary: dict) -> None
    def runs(self, experiment: str | None = None) -> list[dict];  def items(self, run_id: str) -> list[dict]
```
- **`017_eval.sql`:**
  ```sql
  CREATE TABLE eval_runs (
    id TEXT PRIMARY KEY, experiment TEXT NOT NULL, config TEXT NOT NULL, summary TEXT,
    started_at TEXT NOT NULL, finished_at TEXT
  );
  CREATE TABLE eval_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    data TEXT NOT NULL, created_at TEXT NOT NULL
  );
  ```
- `scripts/eval_tokens.py` is rewritten on top of `Harness` (same behaviour, with its own tests kept), proving the abstraction.
- [ ] **Tests:**
  - `decide()`, the same matrix as the eval_tokens test plus `files_root`.
  - The marker restore after a simulated crash (write the marker, construct a new harness, settings restored).
  - The refusal when the app is up (a fake health endpoint).
  - `Results` round trip.
  - `run_task` with fake services: the approvals answered, the check called, usage collected.
- [ ] Implement, then **commit** `feat(eval): shared harness and results store`.

### Task A2: The E1 task suite and checkers
**30 tasks**, each with a programmatic checker:

| App | Tasks (`name`: goal → check) |
|---|---|
| Notepad (6) | `np_haiku`: write a haiku, save as X → file ≥ 5 words; `np_list`: shopping list of 5 items, one per line → 5 non-empty lines; `np_edit`: append a line to an existing file → line present and original kept; `np_replace`: replace "cat" with "dog" everywhere → no "cat", the count of "dog" matches; `np_copy`: copy file A's text into a new file B → equal content; `np_date`: write today's date → parsable date equal to today |
| File Explorer (6) | `ex_folder`: new folder → is a directory; `ex_rename`: rename a file → the new name exists, the old one doesn't; `ex_move`: move 3 files into a folder → all moved; `ex_zip`: compress a folder → zip exists with N entries; `ex_sort`: create folders by file type and move files → each file in its type folder; `ex_props`: report a file's size in the reply → the reply contains the size (checked against `stat`) |
| Calculator (3) | `calc_mul`: 1234 × 5678 into result.txt → 7006652; `calc_sqrt`: √2025 → 45; `calc_conv`: 5 miles in km (Calculator's converter) → 8.04–8.05 |
| Settings (4) | `set_display`: open Display → the Settings window title contains "Display" (via a Windows-MCP snapshot); `set_dark`: switch apps to dark mode → registry `AppsUseLightTheme == 0` (read-only check), **restored to the original value after**; `set_bt`: open the Bluetooth page → title; `set_sound`: open Sound → title |
| Edge (5) | `edge_url`: open example.com → the Edge title contains "Example Domain"; `edge_search`: search for X → the title contains X; `edge_tab`: open two sites in tabs → 2 tabs (Windows-MCP snapshot); `edge_dl`: download a known small file from a local test page → the file is in Downloads; `edge_form`: fill a local test form → the local test server received the values |
| Firefox / YouTube (3) | `ff_open`: open Firefox to example.com → title; `yt_play`: play a lofi video → the Firefox title contains "YouTube" and audio plays (the window title changes from "YouTube" to the video's title); `yt_search`: search YouTube for X → the title contains X |
| Word, needs Office (3) | `w_essay`: write 150 words on X and save → .docx exists, ≥ 150 words (python-docx read); `w_title`: a document with a Heading 1 title → heading style present; `w_table`: a 3×3 table → table present |

- The local test pages (`edge_dl`, `edge_form`) are served by a tiny `http.server` that the harness starts on a random localhost port and allowlists in the task's window list.
- Every task's `files_root` is `Documents\Aethel\eval\<name>\`. Setup creates its inputs and checkers read only there.
- The **checkers are unit-tested** against hand-made good and bad workspaces, with no desktop needed.
- [ ] Implement `e1_tasks.py` + `checks.py`. Test every checker with one passing and one failing workspace (60 cases). **Commit** `feat(eval): E1 task suite with independent checkers`.

### Task A3: The E1 conditions, learning curves, the runner
- **Conditions (spec §14):**

  | Condition | `system1.enabled` | `use_learned_skills` | `use_macros` |
  |---|---|---|---|
  | (a) LLM only | false | false | false |
  | (b) + System 1 | true | false | false |
  | (c) + skills and notes | true | true | false |
  | (d) + macros | true | true | true |

  - (c) and (d) use `fresh_knowledge=True`: the knowledge folder is created empty per condition, so the learning curve starts from zero.
  - The new setting `use_macros: bool = True`: when it's off, `_macro_for` returns None, but skills still show as hints.
- **Run order:** for each condition, for rep 1..5, every task in a fixed order. This gives a learning curve per task across reps for (c) and (d).
- **Per item:** success (the check, else the task's verdict), steps (LLM vs macro), wall time, LLM calls and tokens (`llm_calls` by `task_id`), the skill used, whether a macro replayed, and the handover reason.
- **Summary per condition:** the success rate, the mean steps / time / tokens per rep (that's the learning curve), and the replay rate per rep.
- **Cost:** shown only if `settings.prices[provider:model]` is set by the user; otherwise "not measured".
- **The CLI:** `scripts/eval.py e1 --conditions a,b,c,d --reps 5 [--tasks np_*,calc_*] [--base-url … --model …]`. It prints progress and writes `eval_runs` + `~/.aethel/eval/e1-<run>.json`.
- [ ] **Tests:**
  - The condition patches.
  - `use_macros` off → no replay even with a compiled macro (engine test).
  - Fresh knowledge isolation (two conditions don't see each other's skills).
  - The summary maths on synthetic items.
  - Run order.
- [ ] Implement, then **commit** `feat(eval): E1 conditions and learning curves`.

### Task A4: E2, the grounding set
- **Collection:** while any task runs (normal use or E1), each successful pointer step is recorded, where the model or macro chose an element from a fresh snapshot, if `settings.eval.collect_grounding` is on (default **off**; E1 turns it on). The record is `(snapshot elements, step description, chosen element, task outcome)`, written to `eval_items` under a `grounding` collection run. Each case is ~2–6 KB; at most 400 are kept.
- **Labelling:** `scripts/eval.py e2 label` walks the cases in the terminal. It shows the instruction, the 10 nearest candidates and the recorded choice. The user confirms, picks another, or marks it ambiguous. Only confirmed cases go into the test set ("silver labels from successful runs, confirmed by hand").
- **Conditions:**
  - Laya (`ground()` with the Phase 2b candidate list).
  - The `agent` LLM (the same candidates as a numbered list; a choice by number).
  - Jev, if a TypeSafe key is present; otherwise "not measured".
- **Metrics:** top-1 accuracy, ECE (Laya probabilities; the LLM has none, so "n/a"), and p50/p95 latency. `ground_threshold` gets its first calibration, chosen like the skill threshold, and replaces the "not yet calibrated" comment.
- [ ] Tests (recording only on successful pointer steps, the cap, the labeller's state transitions, metric functions), implement, then **commit** `feat(eval): E2 grounding set and measurement`.

### Task A5: E3, E4, E5 under the harness
- **E3** (voice) and **E4** (identity): move `scripts/eval_voice.py` and `scripts/eval_identity.py` logic into `eval/e3.py` and `eval/e4.py`, writing `Results`. Their scripts become thin wrappers.
- **E5, semantic memory:**
  - **Data:** `backend/aethel/eval/e5.json` holds 6 scripted multi-session "lives". Each has 3–4 sessions of 6–10 user turns that mention facts naturally, with corrections and distractors. Then 5 recall questions in a fresh conversation with expected answers (keywords, any-of) and 2 questions about things never said (expected: "don't know").
  - **Conditions:** memory off (`facts_enabled=false`, `episodic_enabled=false`), facts only, facts + episodic.
  - **Run:** feed the sessions through `ChatService.run_turn` (real chat model, real extraction), wait for the extractor, then ask the questions.
  - **Score:** keyword match per answer plus a check for hallucinated answers on the never-said questions. Optionally an LLM judge with the rubric in `e5.json` (`--judge`), reported separately.
  - **Metrics:** recall accuracy, the false-memory rate, and the facts stored per session (from `fact_events`).
- [ ] Tests (the scoring functions, the condition patches, E5 orchestration with a scripted LLM), implement, then **commit** `feat(eval): E3–E5 on the shared harness`.

### Task A6: The evaluation dashboard
- **`GET /api/eval/runs?experiment=`**, **`/api/eval/runs/{id}`** (summary + items, paged).
- **The screen** (`ui.Screen` adds `eval`, reached from Settings → "Evaluation" and the palette):
  - **E1:** a success-rate table (rows: conditions; columns: app groups), plus **learning-curve charts** (hand-rolled SVG: mean tokens, steps and wall time per rep for (c) and (d), against (a) as a dashed baseline). Below, a per-task drill-down of the reps with replay markers.
  - **E2:** a top-1/ECE/latency table per condition, and a reliability diagram (SVG) for Laya.
  - **E3:** p50/p95 latency per condition and the false cut-off rate. A latency histogram.
  - **E4:** an identity-score histogram per condition, τ_id marked, and the regenerate rate.
  - **E5:** recall accuracy and false-memory rate per condition.
  - **System 1 thresholds:** a table of every measured threshold, with its source script, date and held-out numbers (read from `~/.aethel/eval/*.json`).
  - **Token usage:** reuses the Usage view, plus a per-condition tokens comparison.
  - Every empty panel shows "Not measured yet: run `py -3.11 scripts/eval.py e…`".
- [ ] Vitest (each panel with fixtures and empty states, the chart scales), implement, then **commit** `feat(eval): evaluation dashboard`.

### Task A7: `docs/evaluation.md`, verify, PR 6a
- `docs/evaluation.md`: for each experiment, the question, the conditions, the data, the exact command, the metrics, known threats to validity (small n, silver labels, a single-machine desktop, model nondeterminism), and how to read the dashboard.
- [ ] Suites → exit 0. Review subagent, then fixes.
- [ ] **The runs themselves are the user's call** (hours of desktop time and tokens). The PR ships the tools. The results are added as they're run, in follow-up commits to `docs/evaluation.md` and the dashboard, which reads them live.
- [ ] Open the PR "Phase 6a: evaluation" and bind it.

---

# Part B: Polish (PR 6b)

## File map (Part B)
| File | Change |
|---|---|
| `frontend_app/src/features/onboarding/*`, `backend/aethel/api/routes/setup.py` | first run |
| `frontend_app/src/features/palette/*` | ⌘K palette |
| `backend/aethel/teach/recorder.py`, `teach/compose.py`, `api/routes/teach.py`, `frontend_app/src/features/teach/*`, overlay badge | teach mode |
| `backend/aethel/api/routes/permissions.py`, `frontend_app/src/features/settings/PermissionsSection.tsx` | permissions editor |
| legacy `backend/*.py`, `requirements.txt`, `requirements-images.txt` | legacy deletion; optional extras |
| `README.md`, `docs/architecture.md`, `docs/third_party.md`, `docs/privacy.md` | docs |

### Task B1: First-run onboarding
- **`GET /api/setup`:** `{onboarded, has_cloud_key, local_endpoint: {url, ok, model}, gguf_files: [...], personas}`.
  - It probes `settings.custom_base_url` and the local llama-server for `/v1/models`.
  - It lists `models/llm/*.gguf`.
- **The flow** (full-screen, Paper & Ink, gentle springs):
  1. **Welcome:** one serif line plus "Begin".
  2. **How Aethel thinks:**
     - "Hybrid": enter keys for Groq/Gemini/OpenRouter, each with an inline test using the existing `/api/providers/{id}/test`.
     - "Fully local": shows the detected endpoint or GGUF files. It offers "Use my running server at …" (sets custom + roles, like the user's own setup) or "Pick a model file".
  3. **Meet your companion:** use Aethel, or "Create a persona", which opens the studio create flow.
  4. **Done:** `settings.onboarded = true`.
- It's skippable at every step ("Set up later"). It's shown only when `onboarded` is false **and** no keys and no local endpoint answer, so existing users never see it.
- [ ] Tests (each step, skip, the probe results, existing-user bypass), implement, then **commit** `feat(onboarding): first run`.

### Task B2: The ⌘K palette
- Ctrl+K (⌘K on mac) opens a Radix dialog with a fuzzy-filtered list. The fuzzy filter is a small subsequence-scoring function, with no library.
- **Commands:**
  - New conversation.
  - Switch persona (one entry per persona).
  - Start a call.
  - Run a skill: approved skills, which start a task with the skill's intent; parameterised templates prompt for their `{pN}` values inline.
  - Open a screen (conversation, memory, tasks, personas, settings, evaluation).
  - Toggle private mode, toggle web for this conversation, stop all tasks (the kill switch).
- **Keyboard:** arrows, Enter, Esc. Recent commands appear first (`localStorage`, try/catch).
- [ ] Tests (filter scoring, each command dispatches, the skill-parameter prompt, recents), implement, then **commit** `feat(palette): command palette`.

### Task B3: Teach mode (D9)
- **The recorder** (`teach/recorder.py`):
  1. "Teach Aethel" (palette or the Memory → Skills "Teach" button) asks for a short name and the goal ("how to export a PDF from Word").
  2. Recording starts, with a visible overlay badge ("Watching · Esc to stop", in the overlay window from Phase 2b).
  3. A `pynput` low-level listener records clicks and key presses.
  4. **For each click,** the element under the point is resolved from a Windows-MCP `Snapshot` taken right **before** the click: the nearest element whose box contains the point, recorded as `{role, name, window}`, exactly the macro target shape.
  5. **Keys:** typed text is grouped into `win_type` steps per focused element. Shortcuts become `win_shortcut` steps. **Keys go nowhere when the focused element is a password field** (UIA `IsPassword` via the snapshot's element info): the step is recorded as "(typed a password: you'll be asked each time)", and the step text is never stored.
  6. Esc or Ctrl+Alt+Esc stops it.
- **`teach/compose.py`:**
  1. Turns the recording into a **draft skill**: steps as text plus a recorded step list in `recent_structures`.
  2. It's saved as `status: quarantined`, `origin: taught`, with a provisional `macro_def` that's **not compiled**, per D9: it compiles only after 3 verified runs with the same structure, like any skill.
  3. The user reviews it in Memory → Skills: the steps as text, editable, then "Approve".
- **Privacy:** the recording lives in memory only until it's saved. A "Discard" button drops it, with nothing written.
- [ ] **Tests:**
  - compose: a click on a resolved element becomes a `win_click` with a target; typing groups per element; a password field drops the text; an Esc stop; the draft skill frontmatter.
  - The recorder with a fake listener and a fake snapshot source.
  - The UI flow (start, the badge state, review, approve or discard).
- [ ] Implement, then **commit** `feat(teach): learn a skill by watching you`.

### Task B4: The permissions editor
- **`GET /api/permissions`**, **`PUT /api/permissions`:** validated against the manifest schema, written atomically, and reloaded live by `Permissions`.
- **The Settings section:**
  - Lists of allowed read and write folders (add with a folder picker through Tauri's dialog, remove).
  - The hard-denied paths are shown read-only, with the reason.
  - The shell policy (allowed interpreters and commands).
  - "Reset to defaults".
- Changes take effect for the next task and never for a running one: the engine reads permissions per call, so the editor shows a note when a task is running.
- [ ] Tests (validation rejects relative paths and duplicates, atomic write, live reload, UI add/remove), implement, then **commit** `feat(settings): permissions editor`.

### Task B5: Delete the legacy backend
1. **Inventory:** list every `backend/*.py`. For each, run `grep -rn "import <module>\|from <module>" backend/aethel scripts frontend_app/src-tauri` → must be empty.
2. **Port the last legacy constants still read:** `config.BASE_PERSONA` (the Rosia migration) becomes `personas/legacy.py`.
3. **Delete:** `server.py`, `app.py`, `agent_loop.py`, `knowledge_store.py`, `skill_engine.py`, `rule_engine.py`, `rule_store.py`, `prompt_builder.py`, `generation.py`, `memory.py`, `embeddings.py`, `reranker.py`, `graph_memory.py`, `emotion_engine.py`, `persona_distillation.py`, `image_generator.py`, `gguf_text_encoder.py`, `tts_engine.py`, `stt_engine.py`, `media_handler.py`, `mcp_executor.py`, `permission_manifest.py`, `transaction_log.py`, `summarizer.py`, `state.py`, `config.py`, `classify_memories.py`, `build_dataset.py`, `build_memory_db.py`, `clean_datasets.py`, `eval_harness.py`, `benchmark_agent.py`.
   - Its 10 fs tasks are folded into E1 as `fs_*` tasks if still useful; otherwise they're dropped and noted.
4. **`requirements.txt`:** remove `pandas`, `pyarrow`, `lancedb`, `networkx`, `torch`, `sentence-transformers`, `transformers`, `diffusers`, `compel`, `accelerate`, `gguf` and `sentencepiece` from the base install. `requirements-images.txt` holds the local Z-Image extras (torch, diffusers, accelerate, gguf, sentencepiece). The installer offers it as an option.
5. **Verify:** a fresh venv with the base requirements, the backend starts, the full pytest suite passes, and the app boots.
- [ ] **Commit** `chore: remove the v1 backend; slimmer base install`.

### Task B6: Docs
- **`README.md`:** what Aethel is (the USP in one paragraph: *skills graduate from prompts into programs*), screenshots, install (base and optional image extras), the models it uses and where they live, hybrid vs fully local (including "point it at your own llama-server"), privacy (what leaves the machine in each mode), the kill switch, and how to run tests and evals.
- **`docs/architecture.md`:** the package map, the request flows (chat turn, task, macro replay, voice turn, image), the data on disk, and the events.
- **`docs/privacy.md`:** a per-feature table of local vs cloud, what's stored and how to delete it (facts, episodes, `fact_events`, gallery, `llm_calls`), and a "Delete all my data" pointer, which needs a Settings button: add it here, with a typed confirmation.
- **`docs/third_party.md`:** every bundled or downloaded model and package with its license.
- [ ] Commit `docs: README, architecture, privacy, third-party`.

### Task B7: Verify, demo, PR 6b
- [ ] Suites → exit 0, plus a fresh-venv install check. Review subagent, then fixes.
- [ ] **Demo** (spec §15 Phase 6):
  1. A first run in a clean `AETHEL_HOME`.
  2. The palette.
  3. Teach a skill and run it 3 times until it compiles.
  4. The dashboard showing whatever has been measured.
- [ ] Open the PR "Phase 6b: polish" and bind it. Update the memory file: **v2 complete**.

---

## Self-review
- **Spec §14 coverage:**
  - E1 → A2, A3.
  - E2 → A4.
  - E3 → Phase 4 B6 + A5.
  - E4 → Phase 5 B6 + A5.
  - E5 → A5.
  - The dashboard → A6.
  - The honesty rule → global, plus A6's empty states.
- **§12.3 items 7–9:** onboarding → B1, palette → B2, dashboard → A6.
- **§13 desktop integration:** E1's suite doubles as the manual desktop integration run.
- **§15 Phase 6:** teach mode → B3, README/docs → B6, plus legacy deletion (§2.1) → B5.
- **Names used across tasks:** `Harness`, `TaskSpec`, `Condition`, `decide`, `Results`, `use_macros`, `AETHEL_KNOWLEDGE_DIR`, `eval_runs`/`eval_items` and `collect_grounding`.
