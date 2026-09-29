# Aethel v2 · Phase 2b (Macros, Ghost Cursor, Replay): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Execution:** inline, the same as 2a. Each task gets exact files, interfaces and test assertions, and its commit holds the code.

**Goal:** Repeated desktop tasks get fast. A skill that succeeds 3 times the same way compiles into a macro that replays without the language model. System 1 grounds each element on the live screen, and any drift hands control back to System 2, which then repairs the macro. You can watch it happen (the ghost cursor) and review it afterwards (the replay timeline).

**Architecture:**
- **Desktop steps record what they touched.** Each step stores the element's role, name and window, as resolved from the last snapshot, plus a thumbnail.
- **Compilation is deterministic code** (`runtime/macro.py`). It reads a successful task's step log and writes a fenced `macro` YAML block into the skill. Elements are addressed by `{role, name, window}`, never by coordinates.
- **Execution:**
  - For each step, take a fresh snapshot and ground the element: an exact match first, then a System 1 Choice over the candidates plus "none of these".
  - Every step then runs through the engine's normal `_handle`, with the same tiers, approvals and step rows.
  - Drift, a failed step or a missing parameter hands over to the LLM loop with the progress so far.
- **The overlay** is a second Tauri window: transparent, always on top and **fully click-through** (user decision). It follows `cursor_intent` events.

**Tech Stack:** Pillow 12 (thumbnails), Tauri v2 multi-window, the existing Laya System 1, and Windows-MCP.

**Spec:** §4.4, §4.5, §5.2 (element grounding, macro drift), §6.3 (tier 3, compilation, execution, repair), §15 (Phase 2 demo).

**User decisions (2026-09-30):**
- **The overlay is fully click-through.** Stopping stays on Ctrl+Alt+Esc and the task panel. This changes spec §4.4, which put a clickable kill button on the overlay.
- **Replay thumbnails are on by default,** with a switch in Settings → Learning. They're stored only under `~/.aethel/media/tasks/<task>/`.

## Global Constraints
- Every earlier Global Constraint still applies.
- **Macros never skip safety.** Every macro step goes through `decide()`, approvals and the step log, exactly like an LLM step.
- **No coordinates in macros.** Steps address `{role, name, window}`. A step recorded without a resolvable element makes the run uncompilable.
- **Generated content isn't compiled.** A run is uncompilable if it typed or wrote text that isn't in the goal (a haiku, an essay), so macros cover navigation, not writing.
- **Thresholds are measured or labelled.** `skill_threshold` gets its calibration set in this phase, and the grounding threshold is marked uncalibrated until E2.

---

### Task 1: Step metadata, decider, thumbnails
**Files:** `store/migrations/008_step_meta.sql` (`steps.meta`, `steps.decider`, `steps.thumbnail`), `tools/base.py` (`ToolResult.meta: dict | None`, `ToolResult.thumbnail: bytes | None`), `runtime/store.py`, `runtime/engine.py` (saves the thumbnail under `media/tasks/<task>/<step>.jpg`; `decider` = `agent` | `macro`), `tools/desktop.py`, `settings.py` (`replay_thumbnails: bool = True`).

**What the desktop pointer tools record:**
- `meta = {"element": {role, name, window}, "app": name}`, resolved from the snapshot at call time
- a ≤480 px JPEG `thumbnail` taken with Windows-MCP `Screenshot` after the action, when the setting is on

**Tests:**
- meta and thumbnail are persisted, and the file is written
- with the setting off, no screenshot call is made
- decider defaults to `agent`

### Task 2: Ghost cursor
**Files:**
- `protocol.py`: `CursorIntent {task_id, x, y, label}` (physical pixels)
- `tools/desktop.py`: `on_pointer` callback, called before Click/Move/Scroll/MultiSelect and a located Type
- `services.py`: wires it to `hub.publish`, with the task id taken from `ToolContext`
- `frontend_app/overlay.html`
- `src/overlay/*`: its own socket; a spring-animated dot plus caption; hides when no task is running
- `src-tauri`: the overlay window (transparent, undecorated, always on top, skip taskbar, primary-monitor size, `set_ignore_cursor_events(true)`, hidden until a task runs); the overlay capability
- `vite.config.ts`: a second input

**Tests:**
- a cursor intent is published before the click, carrying the task id
- vitest: the overlay reducer maps physical pixels to CSS pixels through `devicePixelRatio`, shows on a cursor intent, and hides on a terminal `task_state`

### Task 3: Macro compilation
**Files:** `runtime/macro.py`, `memory/rsm.py` (a `recent_structures` frontmatter field, `set_macro(id, macro, status)`, the macro block in the body), `runtime/reflect.py` (after a verified success with a chosen skill, record the run's structure and compile once the last 3 runs match).

**Produces:**
- `structure(steps) -> tuple`: the ok, state-changing steps as `(tool, role, name)`; observation steps are dropped
- `compile_macro(goal, steps) -> dict | None`, where the dict is `{template, params, steps: [{tool, args, target?}]}`:
  - a literal that appears in the goal becomes `{p1}`, `{p2}` and so on
  - `template` is the goal with those values replaced, used later to bind new goals
  - the result is `None` if any typed text isn't in the goal, or if a pointer step lacks an element
- `bind(template, goal) -> dict | None`

**Tests:**
- lofi-style log → macro with a `{p1}` query and targets by role and name
- a haiku-writing log → `None`
- binding "play jazz on YouTube in Firefox" against the template → `{p1: "jazz"}`
- a mismatch → `None`
- 3 matching structures compile; a differing one resets the count
- the macro round-trips through the skill file

### Task 4: Macro execution, drift and repair
**Files:** `runtime/macro.py` (`ground`, `run_macro`), `runtime/engine.py`, `settings.py` (`system1.ground_threshold`, marked uncalibrated).

**Behaviour:**
- **When:** the chosen skill has `macro: compiled` and its template binds to the goal.
- **Before planning:**
  - the plan becomes the macro's step descriptions
  - each step is grounded (snapshot → exact `(role, name)` in the window → System 1 Choice over the top 15 candidates plus "none" ≥ threshold → otherwise drift) and executed through `_handle`, marked `decider=macro`
- **On drift or failure at step k:**
  - the LLM executor takes over with "Ran the learned macro: steps 1..k-1 done; step k: <reason>. Continue from the current screen."
  - after a verified success, the macro is **repaired**: recompiled from this run's successful steps
  - two failed repairs in a row mark it `broken`, and the skill then runs LLM-guided

**Tests (fake desktop and System 1):**
- a clean macro run makes zero LLM executor calls, and its steps are marked `macro`
- an exact-match miss followed by a confident System 1 pick → continues
- a confident "none" → handover, with the progress note in the executor prompt
- a repair rewrites the macro
- two failures → `broken`
- an approval-gated step still waits for approval

### Task 5: Calibrate skill selection
**Files:** `eval/s1_skill.jsonl` (about 30 goals × candidate skill lists, labelled with the right skill or "none"), `scripts/eval_s1_skill.py` (the dev/test split and threshold rule from 2a), `settings.py` (a measured `skill_threshold`), `tests/test_eval_s1.py`.

### Task 6: Tasks screen (replay timeline and export)
**Files:**
- `api/routes/tasks.py`: `GET /api/tasks/recent`, `GET /api/tasks/{id}/steps/{step}/thumbnail`, `GET /api/tasks/{id}/export` (a zip of `task.json` plus images); deleting a task's media with the task is deferred until task deletion exists
- `frontend_app/src/features/history/`: `TasksView` (the list), `Timeline` (a step list with the thumbnail loaded as a blob with auth, tool, target, decider badge, duration; arrow keys scrub)
- the rail entry
- `settings`: the thumbnails switch

**Tests:**
- REST: recent tasks, thumbnail bytes, 404s, zip contents
- vitest: the timeline renders the steps, arrow keys move the selection, the decider badge appears

### Task 7: Docs, checks and the demo
- README "Macros, cursor and replay".
- Run every automated check.
- End-to-end run on the real models with a scripted LLM, a fake desktop and real compilation.
- Manual demo: run "play lofi on YouTube in Firefox" 4 times. Runs 1–3 are LLM-guided with the skill; the macro compiles after run 3; run 4 replays it with the ghost cursor visible and a falling duration in Memory.

## Execution notes
- **Task 2:** the overlay is an Aethel window that covers the screen, so the "never operate Aethel's own window" rule would have denied every click. Window lookup now looks through click-through (`WS_EX_TRANSPARENT`) windows; this is tested against a real Win32 window. The overlay is created hidden and shown only after click-through is applied, so a failure can't cover the screen.
- **Task 3 (parameters):** typed text is rarely the goal verbatim (a search URL, for example). A parameter is therefore a run of goal words that isn't part of the skill's own title, intent or apps and that was typed. URL-like text uses the `+` form.
- **Task 4 (taint):** a replayed step's result is read by no model, so it no longer taints the next macro step. Without this change every replayed write asked for approval. A failed step's error text does reach the LLM in the handover, so it is wrapped as untrusted and taints from then on.
- **Task 4 (checks):** macro runs don't call the planner, so they have no checks. A replay counts as succeeded when every step went through.
- **Task 5 (measured):** Laya never picked a wrong skill at any threshold. 0.3 was chosen on dev; held out, precision is 1.00 and coverage 0.89. Its misses are confident "none"s, so a separate 0.95 bar decides when "none" hides the maybe-helpful skills: that hid 9 of 12 no-fit goals and 0 fitting ones (in-sample).
- **End to end on the real models** (scripted LLM, the real Desktop adapter over a fake Windows-MCP):
  - Runs 1–3 of "play … on YouTube in Firefox" took 8 LLM calls each.
  - The macro compiled after run 3.
  - Run 4 replayed every step with 1 LLM call (the background reflection) and typed `search_query=study+beats`.
  - 16 thumbnails were saved.

## Deferred
- The 300-case element-grounding set (E2)
- Fine-tuning Laya on Aethel's labelled decisions
- A clickable overlay stop button (declined by the user)
- Macro steps for Office, file and shell tools (desktop only for now)
