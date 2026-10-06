# Aethel v2 · Phase 3b-2 (Tool Schemas): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Execution:** inline, as in 3a and 3b-1. Branch `v2-phase3b-schemas` off `main`.

**Goal:** Stop sending ~9k tokens of tool schemas with every agent call. Do it without ever making a tool unreachable.

**Architecture:**
1. **Measure each tool's schema exactly,** using llama-server's `/tokenize` when it's available, else chars ÷ 4.
2. **Compact schemas losslessly:** strip JSON-schema noise, and use curated short descriptions for the verbose upstream tools.
3. **Tool groups on demand:** each call carries the **core** tools, plus a one-line catalogue of the other groups and a `use_tools(group)` meta-tool. Asking for a group adds it from the next model turn onwards. Nothing becomes unreachable, so the change is lossless in capability. What could change is how quickly the model finds a tool, and the eval checks that.

**Tech Stack:** Python (`tools/`, `runtime/`), the existing `llm_calls` accounting, and `scripts/eval_tokens.py` (extended with a second A/B axis).

**Spec:** `docs/superpowers/specs/2026-09-30-aethel-v2-token-efficiency-design.md` §6 ("deferred: send only relevant tool groups", now decided by the measurement in the roadmap §1). See also `docs/superpowers/plans/2026-10-06-aethel-v2-roadmap.md` §3 (carry-overs).

## Global Constraints
- Every earlier constraint still applies (see the roadmap §6).
- **No tool may become unreachable.** Every group is always reachable through `use_tools`, and the catalogue line is always in the prompt.
- **Compaction must not remove information the model uses to choose or call a tool:** every parameter name, type, enum, required flag, and the first sentence of each description stays. Curated descriptions are reviewed by hand and stored in code (not generated at runtime).
- The setting `token_saving.tool_groups: bool` **defaults to off** until Task 6's eval shows that success doesn't drop. Compaction (`token_saving.compact_schemas`) defaults to on, because it's lossless by construction (Task 2's test proves it).
- Approvals, tiers, taint and the step log are unchanged by grouping: a tool's handler and assessor are the same whichever turn it's offered in.

---

## File map
| File | Change |
|---|---|
| `scripts/eval_tokens.py`, `scripts/eval_extract.py`, `backend/tests/test_eval_s1.py` | carry-overs: `--base-url`, the window allowlist, `applicationframehost` (cherry-pick `b88f3fd`, `081bc0c` from local `v2-phase3b`, then the edit) |
| `scripts/tool_schema_report.py` (new) | per-tool token report |
| `backend/aethel/tools/compact.py` (new) | `compact_schema()`, `compact_spec()` |
| `backend/aethel/tools/descriptions.py` (new) | curated short descriptions for Windows-MCP, Office and Desktop Commander tools |
| `backend/aethel/tools/base.py` | `Tool.toolgroup: str = "core"` |
| `backend/aethel/tools/registry.py` | `specs(groups=None, compact=False)`, `groups()` |
| `backend/aethel/tools/groups.py` (new) | `GROUPS` catalogue, `USE_TOOLS` spec, `catalogue_line()` |
| `backend/aethel/tools/desktop.py`, `office.py`, `file_commander.py`, `local_fs.py`, `shell.py`, `launcher.py` | set `toolgroup`; use curated descriptions |
| `backend/aethel/runtime/engine.py`, `runtime/prompts.py` | offer core + requested groups; handle `use_tools`; the planner lists groups instead of every tool |
| `backend/aethel/settings.py` | `token_saving.compact_schemas`, `token_saving.tool_groups`; `RouteEntry.reasoning` |
| `backend/aethel/context/builder.py` | the reasoning reserve |
| `frontend_app/src/features/settings/RolesSection.tsx`, `LearningSection.tsx`, `lib/types.ts` | "Thinks before answering" checkbox; "Send only the tools a task needs" switch |

---

### Task 0: Carry-overs
- [ ] **Step 1:** `git switch -c v2-phase3b-schemas main`, then `git cherry-pick b88f3fd 081bc0c`. If there's a conflict in `scripts/eval_tokens.py`, keep the cherry-picked version: `main` has the older one.
- [ ] **Step 2:** In `scripts/eval_tokens.py`, set `WINDOWS["calc"] = ("calculator", "applicationframehost", "notepad", "save as")` and `WINDOWS["display"] = ("settings", "applicationframehost")`, with a comment saying Store apps report their host window.
- [ ] **Step 3:** Add to `test_token_eval_only_approves_the_task_own_windows`:
```python
    assert tok.decide("calc", "win_type", "Type “1234” in applicationframehost", "write") == "allow_once"
    assert tok.decide("haiku", "win_type", "Type “x” in applicationframehost", "write") == "deny"
```
- [ ] **Step 4:** Run `cd backend && py -3.11 -m pytest tests/test_eval_s1.py` → exit 0.
- [ ] **Step 5: Commit** `fix(eval): carry over the eval-script safety fixes; Store-app windows`.

### Task 1: The per-tool schema report
**Files:** `scripts/tool_schema_report.py`, `backend/tests/test_eval_s1.py`

**Produces:**
```python
def spec_json(spec: ToolSpec) -> str          # exactly what the provider receives for one tool (openai_compat._wire_tools([spec])[0])
def count_tokens(texts: list[str], base_url: str | None) -> list[int]
    # POST {base_url without /v1}/tokenize {"content": t} → len(tokens) when base_url is given and answers;
    # otherwise len(t) // 4, and the report says "estimated"
def report(specs: list[ToolSpec], counts: list[int]) -> list[dict]   # [{"tool", "group", "tokens", "share"}], sorted by tokens desc
```

**Run:**
- It builds services (`build_services()`), starts the MCP hub, and waits up to 180 s for `windows`, `office` and `files`. Then it reports on `registry.specs()`.
- It prints a table, writes `~/.aethel/eval/tool_schemas.json`, and prints totals per group.
- `--base-url http://127.0.0.1:8080/v1` gives exact counts from the user's llama-server. It's read-only and takes no model time.

- [ ] **Step 1: Failing test.** `report()` on three fake specs with counts `[100, 300, 600]` → sorted 600, 300, 100 with shares 0.6, 0.3, 0.1. `count_tokens` with `base_url=None` → `len // 4`.
- [ ] **Step 2:** Implement. **Step 3:** Run `py -3.11 scripts/tool_schema_report.py --base-url http://127.0.0.1:8080/v1` with the user's go-ahead (it only reads), and paste the table into the PR description.
- [ ] **Step 4: Commit** `feat(eval): per-tool schema token report`.

### Task 2: Lossless schema compaction
**Files:** `backend/aethel/tools/compact.py`, `backend/aethel/tools/descriptions.py`, `backend/aethel/providers/openai_compat.py` (no change: compaction happens before `ToolSpec`s reach it), `backend/aethel/tools/registry.py`, `backend/tests/test_compact.py`

**Produces:**
```python
# tools/compact.py
DROP_KEYS = {"title", "$schema", "examples", "default"}   # JSON-schema annotations the model doesn't need to choose/call
def compact_schema(schema: dict) -> dict
    # recursive copy:
    # - drop DROP_KEYS
    # - {"anyOf": [X, {"type": "null"}]} → X (optional-ness is already carried by "required")
    # - a property "description" longer than 200 chars keeps its first sentence (period followed by a space, or a newline)
    # - keeps every property, type, enum, items, required, minimum/maximum
def compact_spec(spec: ToolSpec, override: str | None) -> ToolSpec
    # description = override if given, else the first paragraph of spec.description, cut at 300 chars on a sentence end

# tools/descriptions.py
SHORT: dict[str, str] = {   # local tool name -> curated one/two-sentence description, written by reading upstream
    "win_snapshot": "List the windows and the interactive elements on screen (role, name, centre point). Use it to find what to click or type into.",
    "win_click": "Click at a point (loc [x, y]) or an element's centre from win_snapshot. button: left|right|middle; clicks: 1 or 2.",
    "win_type": "Type text. With loc, click that field first; clear=true replaces its text; press_enter=true submits.",
    "win_app": "Launch, switch to or resize an app window. mode: launch|switch|resize; name: the app, e.g. 'notepad'.",
    "win_shortcut": "Press a key or chord, e.g. 'ctrl+s', 'enter', 'alt+tab'.",
    "win_scroll": "Scroll at a point. direction up|down|left|right; wheel_times: notches.",
    "win_wait": "Wait a number of seconds (duration).",
    "win_wait_for": "Wait until a window or element with this name appears.",
    "win_move": "Move the mouse to a point (loc), optionally dragging.",
    "win_multi_select": "Click several points while holding ctrl (select many).",
    "win_multi_edit": "Type into several fields: [[x, y, text], ...].",
    "win_clipboard": "Read or set the clipboard (mode copy|paste, text).",
    "win_displays": "List the monitors and their resolutions.",
    # dc_* and word_*/excel_* entries are written the same way, from their upstream docstrings
}
```
These are first drafts. Each entry is checked against the upstream description in Step 3, and must keep every parameter's meaning and value set.

- **Registry:** `specs(groups=None, compact=False)`. When `compact` is on, each spec goes through `compact_spec(spec, SHORT.get(name))` and `compact_schema(spec.parameters)`. The result is cached per tool (the specs don't change while a server runs).

- [ ] **Step 1: Failing tests** (`test_compact.py`):
  - **Lossless contract:** for a schema with `title`, `default`, `examples`, `anyOf[str, null]`, an enum, nested `items` and `required`, the result keeps the same property names (recursively), the same types (`anyOf` collapsed to the non-null branch), the same enums and the same `required`.
  - `$schema` and `title` are gone.
  - A 600-character property description keeps its first sentence.
  - `compact_spec` with an override uses it; without one, it cuts at a sentence end and stays ≤ 300 chars.
  - **Every `SHORT` key is an exposed tool name** (import `EXPOSED` from desktop, office and file_commander and check membership), so a renamed tool fails the test.
  - The registry: `specs(compact=True)` is cached (same object on the second call), and `specs()` is unchanged.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. Write `SHORT` for the `win_*`, `dc_*`, `word_*` and `excel_*` tools by reading each upstream description, from Windows-MCP via `session.list_tools()` in the Task 1 report and from `mcp_servers/office.py` docstrings.
- [ ] **Step 4:** Wire it in: `TaskEngine._execute` and `_plan` use `registry.specs(compact=settings.token_saving.compact_schemas)`. Add the setting `compact_schemas: bool = True`.
- [ ] **Step 5:** Run Task 1's report again with compaction on. Record the before/after total in the commit message. Full suite → exit 0.
- [ ] **Step 6: Commit** `perf(tokens): compact tool schemas (lossless)`.

### Task 3: Tool groups and `use_tools`
**Files:** `tools/base.py`, `tools/groups.py`, `tools/registry.py`, every tool constructor (set `toolgroup`), `runtime/engine.py`, `runtime/prompts.py`, `settings.py`, `backend/tests/test_tool_groups.py`, `test_engine.py`

**Produces:**
```python
# tools/base.py
Tool.toolgroup: str = "core"

# tools/groups.py
GROUPS: dict[str, str] = {   # group -> one line for the catalogue (what it's for, so the model knows when to ask)
    "desktop_extra": "more desktop control: drag, multi-select, multi-field typing, monitors",
    "office": "Word and Excel automation (create, read, write and save documents and sheets)",
    "files": "bulk file work: search inside many files, file info, edit part of a file",
    "web": "search the web and read pages, with citations",
    "browser": "your own background web browser: open pages, click, type, fill forms",
}
CORE: set[str] = {"core"}
USE_TOOLS = ToolSpec("use_tools", "Add a group of tools for the rest of this task.",
                     {"type": "object", "properties": {"group": {"type": "string", "enum": list(GROUPS)}},
                      "required": ["group"]})
def catalogue_line(available: set[str], active: set[str]) -> str
    # "More tools on request (call use_tools): office (Word and Excel …); files (…)": only groups that are
    # registered (available) and not yet active; "" when none

# tools/registry.py
def groups(self) -> set[str]                                       # groups with at least one registered tool
def specs(self, groups: set[str] | None = None, compact: bool = False) -> list[ToolSpec]   # None = all
```

**Group assignment:**
- **core:** `fs_*`, `shell`, `open_url`, and `win_app`, `win_snapshot`, `win_click`, `win_type`, `win_scroll`, `win_shortcut`, `win_wait`, `win_wait_for`, `win_clipboard`, `win_locate`.
- **desktop_extra:** `win_move`, `win_multi_select`, `win_multi_edit`, `win_displays`.
- **office:** all Office tools.
- **files:** all `dc_*` tools.
- **web, browser:** Parts B and C, which set them.

**Engine:**
- `_execute` keeps `active: set[str]` per task, stored in `self._groups[task_id]` like `_obs` and reset at `_run` start. It's initialised as follows:
  - With `token_saving.tool_groups` **off**, it's every group, so behaviour is exactly as today.
  - With it **on**, it's `CORE` plus the groups the planner named (below), plus the groups of any learned skill shown to the planner (its `apps` mapped by `{word, excel → office}`).
- **Each call offers:** `registry.specs(active, compact)` + `[COMPLETE_STEP, FINISH_TASK]` + `[USE_TOOLS]` if any group is inactive. The executor system text gets `catalogue_line(registry.groups(), active)` appended. The line is **appended** to the stable system prompt: it only changes when a group is added, so the prefix stays cacheable between additions.
- **`_handle` for `use_tools`:** validate the group. Add it to `active`. Return `"Added {group} tools: {names}."` (`ok=True`, not a step in the log, no approval: it changes what the model can ask for, never what it may do). An unknown group returns an error listing the groups.
- **A call to a tool whose group isn't active** (the model guessed a name from the catalogue): it's treated as an implicit `use_tools`. The group is added and the call proceeds through the normal path. This is the safety net that makes grouping lossless.
- **Planner:** with groups on, the planner prompt lists the **core** tools (name: compact description) plus the catalogue line, instead of every tool. `submit_plan` gains an optional `tool_groups: [group]` array: "groups you expect to need". These seed `active`.

- [ ] **Step 1: Failing tests:**
  - `test_tool_groups.py`:
    - `catalogue_line` lists only available inactive groups, and returns "" when there are none.
    - `registry.specs({"core"})` excludes office tools.
    - `groups()` reflects what's registered.
  - `test_engine.py`:
    - **Off:** the executor's tool list is identical to today's, and there's no `use_tools`.
    - **On:** the first execute call has core tools plus `use_tools` and no `word_*`, and its system text has the catalogue line. After a scripted `use_tools(group="office")`, the next call includes `word_*`, and the catalogue no longer mentions office.
    - **On, implicit:** a scripted direct `word_new` call with office inactive runs (a fake office tool records the call), and office is active afterwards.
    - **On, planner:** `submit_plan(..., tool_groups=["files"])` → `files` is active from the first execute call.
    - `use_tools` adds no step row and needs no approval.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** Full suite → exit 0.
- [ ] **Step 5: Commit** `perf(tokens): tool groups on demand (off until measured)`.

### Task 4: The reasoning reserve
**Files:** `settings.py` (`RouteEntry.reasoning: bool = False`), `context/builder.py`, `frontend_app/src/features/settings/RolesSection.tsx`, `lib/types.ts`; tests in `test_builder.py` and `settings.test.tsx`

- In `budget_for`: `reply = min(max(reply_tokens, 2048 if any(e.reasoning for e in chosen) else 0), base // 4)`.
- The `max_tokens` sent to a reasoning entry is `max(max_tokens, 2048)`. This is applied in `RoleRouter.stream` per entry, because thinking eats the reply budget.
- **UI:** each entry row in Models gets a small checkbox, "Thinks before answering", with the hint "Reasoning models (e.g. Gemma 4, Qwen3 thinking) use tokens before replying; Aethel leaves them room."

- [ ] Tests:
  - The budget with a reasoning entry reserves 2048.
  - The router sends `max_tokens=2048` to a reasoning entry when settings say 1024. Use a ScriptedProvider and check `max_tokens_seen`.
  - The checkbox PATCHes the entry.

  Implement, run the suites, then **commit** `feat(roles): room for models that think before answering`.

### Task 5: Settings and UI for groups
- `TokenSavingSettings.tool_groups: bool = False`, commented "Not measured yet; turned on only if scripts/eval_tokens.py --axis groups shows no drop in success".
- Settings → Learning gets a switch, "Send only the tools a task needs", with the hint "Other tools stay one request away. Saves most of each step's tokens." Plus a vitest test.
- The task panel's activity list shows `use_tools` as a quiet line, "Asked for Word and Excel tools". This is a `ToolActivity`-style row, not a step: emit a `TaskNote {task_id, text}` event. It's new in `protocol.py`, so regenerate the types.
- **Commit** `feat(ui): tool-groups switch and note`.

### Task 6: The A/B, rerun once (masking × groups)
**Files:** `scripts/eval_tokens.py`

- The `--axis masking|groups` flag (default `groups`) picks the arms:
  - `groups`: off = `{tool_groups: false}`, on = `{tool_groups: true}`.
  - `masking`: as today.
- Both arms use `compact_schemas: true`: compaction is lossless, so it's the new baseline.
- The summary adds `mean_tool_tokens` from the `breakdown.tools` of each task's `llm_calls`.
- **Run:**
  - Only with the user's go-ahead, with the PC left alone and the Aethel app closed.
  - `--base-url http://127.0.0.1:8080/v1 --model model.gguf --context 128000 --timeout 900 --reps 2 --axis groups`
  - If the user agrees, a second run with `--axis masking`.
- **Decide:**
  - If the ON arm's success ≥ the OFF arm's success − 1 run (out of 12), set the `tool_groups` default to `True`, with the measured numbers in its comment.
  - Otherwise leave it off and record why.
  - Masking likewise.
  - If the user doesn't run it, the comment and the PR say "not measured".
- [ ] Tests: `schedule()` for both axes, and the arm settings for each axis. **Commit** `feat(eval): A/B on tool groups; record results`.

### Task 7: Verify, review, PR
- [ ] Full pytest, `pnpm test`, `pnpm build`, `cargo test` → exit 0.
- [ ] Review subagent ("sonnet") over the diff, focusing on: can any tool become unreachable; is the prefix stable between group additions; do approvals and taint behave identically for tools added mid-task. Fix the findings.
- [ ] Open the PR "Phase 3b-2: tool schemas", bind it, update the memory file.

---

## Self-review
- **Roadmap carry-overs:** 1 and 2 → Task 0. 3 (reasoning) → Task 4. 4 (A/B) → Task 6.
- **The deferred spec item** (token spec §6) → Tasks 1, 3 and 6. It's built **lossless in capability**: an implicit add on a direct call, plus `use_tools`.
- **Names:** `Tool.toolgroup`, `GROUPS`, `USE_TOOLS`, `catalogue_line`, `registry.specs(groups, compact)`, `compact_schema`, `compact_spec`, `SHORT` and `TaskNote` are used consistently. Parts B and C set `toolgroup="web"` / `"browser"` (roadmap §4).
