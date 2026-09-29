# Aethel v2 · Phase 2a (System 1 and the Learning Loop): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Execution:** inline, the same as Phase 1b. Each task gets exact files, interfaces and test assertions, and its commit holds the code.

**Goal:** Tasks get faster and better with use. A local System 1 model makes the small judgments, and procedural memory (app notes and skills) is learned after every task and used on the next one.

**Architecture:**
- **System 1** runs [Laya](https://huggingface.co/convaiinnovations/laya) in-process: a Python port of [receptron/laya](https://github.com/receptron/laya)'s ONNX inference on `onnxruntime` + `tokenizers`. It's CPU-only and needs no torch. It sits behind a Jev-shaped `System1` interface, so Jev or a fine-tuned checkpoint can replace it.
- **Procedural memory** (RSM, spec §6.3) is Markdown on disk under `~/.aethel/knowledge/{apps,skills}`. A derived bge-small index serves retrieval.
- **The engine** picks a skill before planning. After a task finishes it credits or debits what it used and runs a reflection pass that writes app notes and skills.

**Tech Stack:** onnxruntime 1.20, tokenizers 0.22, huggingface_hub 0.36 (pinned download), sentence-transformers (bge-small-en-v1.5, already cached), React 18.

**Spec:** `docs/superpowers/specs/2026-09-23-aethel-v2-design.md` §5, §6.3, §12.3 (Memory), §14 (E2).

**User decisions (2026-09-29):**
- **Laya only.** SemIf was rejected: it's a CLI with no server, its dependencies are pinned heavily, and it needs the GPU.
- **Phase 2 is split.** 2a is this plan. 2b covers compiled macros, drift checks and repair, element grounding, the ghost cursor, the replay timeline and the lofi-on-YouTube demo.

## Global Constraints
- Every Phase 1 Global Constraint still applies: `py -3.11`, foreground commands, tool-name regex, tiers, untrusted wrapping, and protocol regen after every protocol change.
- **The Laya bundle is pinned.** It comes from `receptron/laya-onnx` at revision `68f27dfe5a27a54fb2b1fefc432f43f972e90868` and is downloaded on first use to `~/.aethel/models/laya/` (about 1.7 GB).
- **Nothing blocks on System 1.** If Laya is missing or fails to load, every use falls back to the Phase 1 behaviour.
- **Every System 1 call is logged** (inputs, outputs, latency) to the `s1_calls` table, for E2.
- **Honesty rule (spec §14):** thresholds come from a measured run on labelled data, never guessed. Anything unmeasured is reported as not measured.
- **Learned text is untrusted.** Skills and notes are written after tasks that may have read untrusted content. They reach the model as hints inside `<untrusted source="learned …">`, and the tiers, approvals and hard rules still gate every action.

---

### Task 1: System 1 (Laya port, interface, call log)
**Files:** `system1/__init__.py`, `system1/laya.py` (a port of `sequence.ts` + `laya.ts`), `system1/service.py`, `store/migrations/006_system1.sql` (`s1_calls`), `settings.py` (`system1_enabled`, `s1_thresholds`), `tests/test_system1.py`.
**Produces:**
- `LayaModel.load(dir)`
- `LayaModel.system_one(state, questions) -> dict`, in Jev's response shape
- `System1(settings, db, model_dir)` with:
  - `async ask(state, questions, purpose) -> dict | None`, which returns `None` when unavailable
  - `async choice(...)`, `async noul(...)`
  - `status()`
  - `ensure_downloaded()`, which runs in the background at startup

**Tests:**
- sequence layout, option rendering, temperature buckets and confidence, checked with a fake tokenizer
- `pyJsonDumps` parity
- **parity with the Python reference** (the numbers from receptron/laya `test/test_model.ts`: billing 0.9415, urgency 1.3886, churn 0.0988, 267 tokens). Skipped when the bundle is missing.
- calls are logged
- when the model is unavailable, `ask()` returns `None`

### Task 2: Calibration set and intent routing
**Files:**
- `eval/s1_intent.jsonl`: about 60 labelled Aethel messages across chat, task and task_control
- `scripts/eval_s1_intent.py`: accuracy, ECE, and coverage/accuracy at each threshold, written to `~/.aethel/eval/s1_intent.json`
- `chat/router.py`
- `api/ws.py`

**Behaviour:**
- `user_message` passes through `route()`.
- **task_control:** only considered while a task runs in that conversation. It maps to cancel or pause.
- **task:** at or above the measured threshold, it becomes `engine.start(client_id=…)`, which the frontend already reconciles through `task_created.client_id`. Otherwise the message goes to chat.
- **Default:** auto-routing is **on only if** the measured accuracy at the chosen threshold is ≥ 0.9 with coverage ≥ 0.5. Otherwise it ships off, and the Task pill stays the only way to start a task. The result is recorded in the plan notes.

**Tests:**
- routing with a fake System 1 (above and below the threshold; task_control only while a task runs)
- when System 1 is unavailable, everything goes to chat
- the eval script's metrics on a fake model

### Task 3: RSM store
**Files:** `memory/embed.py` (a lazy bge-small singleton; tests inject a fake), `memory/rsm.py` (`KnowledgeStore`), `tests/test_rsm.py`.
**Produces:** `KnowledgeStore(root, embed)` with:
- `skills(status=None)`, `notes()`, `get(id)`
- `upsert_skill(fields, status) -> (doc, created)`: `find_duplicate` (cosine ≥ 0.9); on a duplicate it calls `reinforce` and merges pitfalls
- `upsert_note(app, facts) -> doc`: merges lines and dedupes them
- `retrieve_skills(query, k)`: cosine × effective confidence, approved skills only
- `retrieve_notes(query, k)`
- `record_outcome(ids, success, duration_s)`: runs, successes, `duration_history`, `avg_duration_s`, `last_used` and Laplace confidence; auto-deprecates below 34% after 3 or more runs
- `set_status(id, status)`, `save_note_body(app, body)`

Legacy skill files (`times_succeeded` and similar) are read tolerantly and left untouched.

**Tests:**
- round-trip of the spec §6.3 frontmatter
- dedupe and reinforce
- outcome maths and auto-deprecation
- retrieval ranking and status filtering
- legacy file mapping

### Task 4: Skill selection and knowledge in context
**Files:** `runtime/engine.py`, `runtime/prompts.py`, `store/migrations/007_task_knowledge.sql` (`tasks.knowledge`: the JSON list of doc ids used), `tests/test_engine.py`.

**Behaviour:**
- **Before planning:**
  - retrieve the top 5 approved skills and the top 3 app notes for the goal
  - System 1 Choice over those skills plus "none of these"
  - at or above the threshold, that skill is *the* skill; below it, the top 3 are offered as "may help"
- **Planner and executor prompts** get the wrapped `<untrusted source="learned …">` sections.
- The ids used are stored on the task.

**Tests:**
- the chosen skill appears in the planner prompt, wrapped as untrusted
- "none" leaves the prompts unchanged
- with no System 1 or no skills, Phase 1 behaviour is unchanged
- `tasks.knowledge` is persisted

### Task 5: Outcome credit and reflection
**Files:** `runtime/reflect.py`, `runtime/engine.py`, `protocol.py` (`SkillLearned`), `tests/test_reflect.py`.

**Behaviour:**
- **After `done` or `failed`** (never `cancelled`):
  - `record_outcome(task.knowledge, success=state == "done", duration_s=active_seconds)`
  - then, in the background, one `agent` call with the tool `record_learning`
- **`record_learning` input:**
  - `app_notes: [{app, facts[]}]`
  - `skill: {title, intent, apps, params, steps[], pitfalls[]} | null`
- **Given to the reflection call:** the goal, the plan, the step log wrapped as untrusted, and the verification outcome.
- **Results:**
  - notes are upserted
  - skills are upserted as `approved` when `auto_approve_skills` is on (the default) and as `quarantined` otherwise
  - `SkillLearned {task_id, skill_id, title, created}` is emitted
- Reflection is skipped when the task made no tool steps.

**Tests:**
- attribution on done and on failed, and none on cancelled
- the reflection output reaches the store
- a duplicate skill is reinforced, not re-created
- a reflection failure never changes the task's state

### Task 6: Fuzzy postcondition check
**Files:** `runtime/checks.py`, `runtime/engine.py`, `tests/test_checks.py`.

**Behaviour:**
- A new `Check(kind="judge", path, text=claim)` asks System 1 a Noul question over the file's text.
- It passes at or above `s1_thresholds.judge`.
- The detail carries the probability.
- Without System 1 the check is skipped with "not measured" (it passes but is labelled).

**Tests:** above and below the threshold, and when unavailable.

### Task 7: Memory screen
**Files:**
- `api/routes/memory.py`: `GET /api/memory/skills`, `PATCH /api/memory/skills/{id}` (status), `GET /api/memory/notes`, `PUT /api/memory/notes/{app}`, `GET /api/memory/system1` (status)
- `frontend_app/src/features/memory/`: `MemoryView`, `SkillCard` (with a duration sparkline), `NotesTab`
- the rail entry
- a `skill_learned` toast
- tests

**Tests:**
- the REST round-trips
- vitest: skill cards render runs, success and a sparkline; approve and deprecate send a PATCH; a note can be edited

### Task 8: Docs, all checks and the demo
- README "Learning" section.
- Run every automated check.
- Manual demo: run "Open Notepad and type a haiku about rain" twice. The second run uses the learned skill and the Notepad note, and Memory → Skills shows 2 runs with a falling duration.

## Execution notes
- **Task 1:** the Python port matches receptron/laya's reference outputs exactly. On this laptop's CPU it takes about 210 ms per question, loads in 10 s and uses 1.55 GB of RAM.
- **Task 2 (measured on 2026-09-29):** 56 author-written messages. The user can add more to `eval/s1_intent.jsonl` and re-run the script.
  - **Zero-shot phrasing matters.** The first 3-way choice ("chat / task / stop_task", state with the running task) called almost everything "task": task precision was 0.77 at best.
  - **Act.** Tuned on the dev half as a yes/no question with the message alone as state. The threshold was chosen on dev at 0.5. Held out: precision 0.91, recall 0.91, accuracy 0.92. That meets the plan's rule, so `auto_tasks` ships **on**.
  - **Stop.** A choice with the running task in the state (without it, precision fell to 0.57). The threshold of 0.9 was chosen by cost, because a wrong stop destroys work. On all rows it gives P=1.00, R=0.88. **In-sample:** the dev half had too few near-misses to choose it fairly.
  - Routing costs about 0.5 s per message while auto-tasks is on, plus about 0.5 s more while a task runs.

- **Task 3:** importing sentence-transformers alone took about 30 s here. bge-small now runs through ONNX Runtime instead (the official `onnx/model.onnx`, same revision): vectors are identical (cosine 1.000000) and it loads in about 1 s. bge puts unrelated text near 0.5, so app notes need a similarity of 0.6 or more.
- **Task 5:** a real race turned up. `start /b` could spawn a child before `cmd.exe` joined its job. Fixed by creating shell commands suspended and resuming them only once they're in the job.
- **Task 6 (measured):** an end-to-end run on the real model failed a correct task, because a rain haiku scored 0.365 for "is a short poem about rain".
  - Of 5 phrasings, a yes/no choice worked best: 0.90 on 20 labelled pairs, in-sample.
  - Judge checks now fail only on a confident no (P(yes) < 0.2). That rejects 9 of 10 false claims and none of the 10 true ones.
  - `scripts/eval_s1_judge.py` reproduces the numbers.
- **End to end on the real models** (scripted LLM):
  - Run 1 wrote an approved skill.
  - Run 2, a differently worded goal, retrieved it. System 1 picked it at 0.69, and the run credited it.
  - The second reflection reinforced the same skill instead of duplicating it.
- `skill_threshold` (0.6) is still **not calibrated**. It needs labelled skill-selection cases, and it's the first E2 follow-up in 2b.

## Deferred to 2b
- Compiled macros, drift Noul, repair and `broken` status
- Element grounding and window targeting through System 1
- The ghost cursor overlay window
- The replay timeline
- The lofi-on-YouTube demo
- Fine-tuning Laya on Aethel's own labelled decisions (the E2 follow-up)
