# Aethel v2 · Phase 3b (Tokens, Web, Browser): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Execution:** inline, as in 3a. Each task lists exact files, interfaces, core code and test assertions. A task's commit contains its tests. There are three PRs, one per part, each from its own branch off main after the previous one merges.

**Goal:**
- Aethel does the same work with fewer wasted tokens, and can prove it.
- It can answer with cited web sources.
- It can do web tasks in its own background Edge browser, and those tasks get faster the same way desktop tasks do.

**Architecture:**
- **Part A (tokens):**
  - Every LLM call is recorded with its provider-reported usage and a purpose.
  - Lossless cuts first: stable prefixes, and an unchanged state isn't resent.
  - Then superseded-state masking and a `utility` role, each checked before it's switched on.
- **Part B (web):**
  - Native `web_search`/`web_read` tools with an SSRF guard and one numbered source list per turn.
  - Chat gets a bounded tool loop. Tasks get the same tools.
- **Part C (browser):**
  - `@playwright/mcp` (Edge, persistent profile, headless) behind a vetted adapter.
  - Page snapshots count as "page" state for masking.
  - Browser steps compile into macros addressed by `{role, name, host}`.

**Tech Stack:** openai SDK (`stream_options`), SQLite migrations 013–014, `ddgs`, `trafilatura`, httpx, `@playwright/mcp` through npx, React/TanStack/Zustand.

**Specs:**
- `docs/superpowers/specs/2026-09-30-aethel-v2-token-efficiency-design.md` (Part A)
- `docs/superpowers/specs/2026-09-30-aethel-v2-phase3-design.md` §6, §7, §9 (Parts B and C)

## Global Constraints
- Every earlier Global Constraint still applies. Tests run with `AETHEL_MCP=0` and `AETHEL_SYSTEM1=0`, and the fake embedder is autoused. pytest's `-q` hides "passed", so **check the exit code**.
- **Quality first:** a cut is lossless, or it's measured before it's the default. Unmeasured results are labelled "not measured" in settings comments and in the PR.
- **Evals that spend the user's tokens run only with the user's go-ahead** (`eval_tokens.py`, `eval_extract.py`).
- **Web and page content is untrusted:** it's always wrapped, it taints the task, and it's never passed to fact extraction.
- **The agent never enters credentials,** and never runs JS in pages (no `browser_evaluate` / `browser_run_code_unsafe`).
- **Private mode forces web off,** so web tools are never offered to a local model.
- Use the Edit/Write tools for multi-line Python (the Git-Bash heredoc escaping gotcha).
- Roles are `chat` / `agent` / `vision`, plus the new `utility`.

**Deviations from the specs, decided here:**
- The global web toggle is the **existing** `AppSettings.internet` (already in settings and the frontend types), not a new `web.enabled`.
- The browser MCP server starts with the app. Playwright MCP launches Edge only on the first browser tool call, which meets "starts on first use" without a lazy-registration mechanism in the hub. Idle close calls `browser_close`.
- `web_read` returns up to **12,000** characters with a `start` offset for more (token spec §5), not 32,000 at once.

---

# Part A: Token efficiency (PR 1)

## File map (Part A)
| File | Change |
|---|---|
| `backend/aethel/providers/base.py` | `Usage`; `StreamDone.usage` |
| `backend/aethel/providers/openai_compat.py` | `stream_options.include_usage`, usage chunk parsing, one-time fallback |
| `backend/aethel/store/migrations/013_llm_calls.sql` | `llm_calls` table |
| `backend/aethel/usage.py` | `UsageLog`: record, totals, per-task totals, breakdown estimate |
| `backend/aethel/providers/router.py` | `purpose`, `ref`; records one row per call |
| `backend/aethel/chat/service.py`, `memory/extract.py`, `runtime/engine.py`, `runtime/reflect.py` (via engine), `tools/desktop.py` | pass `purpose` and `ref` |
| `backend/aethel/api/routes/usage.py` (new), `api/routes/tasks.py`, `app.py` | `GET /api/usage`, task usage |
| `backend/aethel/tools/base.py` | `Tool.observes` |
| `backend/aethel/context/observations.py` (new) | unchanged-state notes, superseded masking, budget guard |
| `backend/aethel/chat/persona.py`, `context/recipes.py` | time moves to the end of the system prompt |
| `backend/aethel/settings.py` | `TokenSavingSettings`, `use_learned_skills`, `utility` role default |
| `scripts/eval_extract.py`, `backend/aethel/eval/extract_fixtures.json`, `scripts/eval_tokens.py` | guards |
| `frontend_app/src/features/tasks/TaskPanel.tsx`, `features/settings/UsageSection.tsx`, `RolesSection.tsx`, `lib/types.ts` | UI |

### Task A1: Provider usage
**Files:** `providers/base.py`, `providers/openai_compat.py`, `backend/tests/test_openai_compat.py` (add), `backend/tests/fakes.py` (the fakes may attach usage)

**Produces:**
```python
@dataclass
class Usage:
    prompt: int
    completion: int
    cached: int = 0

@dataclass
class StreamDone:
    finish_reason: str | None
    usage: Usage | None = None
```

**Behaviour in `OpenAICompatProvider.stream`:**
- Add `"stream_options": {"include_usage": True}` unless `self._no_usage_option` is set.
- The usage chunk has `choices == []` and a `usage` field. Read `usage.prompt_tokens` and `usage.completion_tokens`, and `cached` from `usage.prompt_tokens_details.cached_tokens` when present (else 0). Today the loop `continue`s on empty choices, so check `chunk.usage` before that.
- If `create()` raises `APIStatusError` 400 whose message mentions `stream_options`, set `self._no_usage_option = True` and retry once without it.

- [ ] **Step 1: Failing tests.**
  - `test_usage_chunk_is_reported`: the SSE body is text chunks, a finish chunk, then `data: {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 64}}}`. Assert the last event is `StreamDone("stop", Usage(120, 7, 64))`, and that the request body has `stream_options == {"include_usage": True}`.
  - `test_no_usage_is_none`: the existing body → `StreamDone.usage is None`.
  - `test_stream_options_rejected_falls_back_once`: the first request returns 400 `{"error": {"message": "Unknown parameter: stream_options"}}` and the second succeeds. There are 2 requests, and the second has no `stream_options`. A third stream on the same provider sends none.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run `py -3.11 -m pytest tests/test_openai_compat.py tests/test_function_calling.py` → exit 0.
- [ ] **Step 5: Commit** `feat(providers): report token usage from streams`.

### Task A2: `llm_calls` and recording in the router
**Files:** `store/migrations/013_llm_calls.sql` (the SQL in the token spec §3), `usage.py`, `providers/router.py`, all callers, `backend/tests/test_usage.py`

**Produces:**
```python
# usage.py
PURPOSES = ("chat_reply", "fact_extract", "plan", "execute", "final_summary", "reflect", "vision_locate", "web_chat")
def estimate_breakdown(messages: list[ChatMessage], tools: list[ToolSpec] | None) -> dict[str, int]
    # {"system": n, "tools": n, "history": n, "observations": n} in estimated tokens (chars // 4);
    # "observations" = tool-role messages; "history" = every other non-system message; tools = json.dumps of the specs
class UsageLog:
    def __init__(self, db: Database): ...
    def record(self, *, role, purpose, provider, model, ref: dict | None, usage: Usage | None,
               breakdown: dict, status: str, latency_ms: int) -> None
        # usage None → prompt = sum(breakdown.values()), completion = None, estimated = 1
    def task_totals(self, task_id: str) -> dict       # {"prompt", "completion", "cached", "calls", "estimated": bool}
    def totals(self, days: int) -> dict               # {"by_purpose": {p: {...}}, "by_day": [{"day", "prompt", "completion", "calls"}]}

# router.py
async def stream(self, role, messages, *, tools=None, on_switch=None, max_tokens=None, temperature=None,
                 purpose: str = "chat_reply", ref: dict | None = None) -> AsyncIterator[StreamEvent]
RoleRouter.__init__(..., usage: UsageLog | None = None)
```

**Recording:**
- Inside the per-entry `try`, remember `t0`, the provider label, and the `StreamDone.usage` seen.
- In a `finally` around each entry's attempt that **started**, record one row. Status is `ok`, `error` (a ProviderError after starting) or `cancelled` (GeneratorExit/CancelledError).
- A retryable failure before the first event records an `error` row with no usage, so failovers are visible.
- Recording must never raise: wrap it in try/log.

**Callers:**

| Caller | `purpose` | `ref` |
|---|---|---|
| `ChatService` | `chat_reply` | `{"message_id": assistant.id}` |
| `FactExtractor._complete` | `fact_extract` | `{"message_id": user_message_id}` |
| `TaskEngine._complete(task_id, messages, tools, purpose)` | `plan` from `_plan`, `execute` from `_execute`, `final_summary`, `reflect` from `_learn` | `{"task_id": task_id}` |
| `Desktop.locate` (vision) | `vision_locate` | `{"task_id": ctx.task_id}` |

- [ ] **Step 1: Failing tests** (`test_usage.py`):
  - `estimate_breakdown` splits system, tools, history and observations as defined.
  - `UsageLog.record` with and without usage (`estimated` flag); `task_totals` sums, and `estimated` is True if any row is.
  - `totals(7)` groups by purpose and by day.
  - Router: a `ScriptedProvider` whose turns end with `StreamDone("stop", Usage(10, 2))` → one row with purpose, ref and usage. A provider that raises retryable before its first event and then fails over → 2 rows (`error`, then `ok`). Closing the stream early (aclosing after the first token) → a `cancelled` row.
  - `ChatService` turn → a `chat_reply` row with `message_id`. An engine task → `plan` and `execute` rows with `task_id`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement and wire `UsageLog(db)` in `services.py` (`Services.usage`). **Step 4:** Full suite → exit 0.
- [ ] **Step 5: Commit** `feat(usage): record every LLM call with its purpose and tokens`.

### Task A3: Usage API and UI
**Files:** `api/routes/usage.py` (new, auth like the others), `api/routes/tasks.py` (the detail gains `usage`), `app.py` (include the router), `frontend_app/src/features/tasks/TaskPanel.tsx`, `features/settings/UsageSection.tsx` (new), `SettingsView.tsx`, `lib/types.ts`; tests in `backend/tests/test_usage.py`, `frontend .../tasks.test.tsx`, `settings.test.tsx`

**Behaviour:**
- `GET /api/usage?days=7` (1–90) → `UsageLog.totals(days)`.
- The task detail JSON gains `"usage": {"prompt", "completion", "cached", "calls", "estimated"}`.
- **TaskPanel footer** (only when `calls > 0`): `12.4k in · 0.9k out · 7 calls`. There's a `≈` prefix when estimated, and `· 3.1k cached` when cached > 0. The number format is `n < 1000 ? n : (n/1000).toFixed(1)+"k"`.
- **Settings → Usage** (after Memory): "Last 7 days". A table with rows per purpose, using friendly labels (`chat_reply` → "Replies", `fact_extract` → "Remembering facts", `plan` → "Planning tasks", `execute` → "Doing tasks", `final_summary` → "Task summaries", `reflect` → "Learning from tasks", `vision_locate` → "Finding things on screen", `web_chat` → "Web answers"). Columns are In, Out, Cached, Calls, plus a Total row. It's empty-state safe.

- [ ] **Step 1:** Failing tests (API shape, footer text, the Usage table rows and total). **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** pytest, `pnpm test` and `pnpm build` → exit 0.
- [ ] **Step 5: Commit** `feat(ui): token usage in the task panel and Settings`.

### Task A4: Lossless cuts
**Files:** `chat/persona.py`, `chat/service.py`, `context/recipes.py`, `tools/base.py`, `tools/desktop.py`, `context/observations.py` (new, the first half), `runtime/engine.py`; tests in `test_chat_memory.py`, `test_observations.py` (new), `test_engine.py`

**Produces:**
```python
# tools/base.py
@dataclass
class Tool: ...; observes: Literal["screen", "page"] | None = None   # state observations (superseded by newer ones)

# chat/persona.py
def system_prompt() -> str                     # the persona only, no time
def time_note(now: datetime | None = None) -> str   # "Current local time: Tuesday 30 September 2026, 21:04."

# context/observations.py
class StateTracker:
    """Per task (or chat turn): the latest content of each state kind, for 'unchanged' notes."""
    def seen(self, kind: str, content: str, step_no: int) -> str | None   # the note when identical to the last, else None
```

**Changes:**
- Chat: the time goes in a final section `Section("time", NEVER_DROP, time_note())` **after** facts and episodes.
  - `test_empty_memory_sends_exactly_the_old_prompt` changes to assert the persona text comes first and the time note last.
  - The planner and executor prompts keep their `now`, which is computed once per run, so they're already stable within a task.
- `tools/desktop.py`: the `win_snapshot` Tool gets `observes="screen"`.
- `engine._end_step`: when `tool.observes` is set and `StateTracker.seen(kind, content, step_no)` returns a note, the model gets `[unchanged since step {n}: same {screen|page} as then]` instead of the content. The step log still stores the real content.

- [ ] **Step 1: Failing tests:**
  - The chat prompt ordering.
  - `StateTracker`: identical → note; different → None and it updates; kinds are independent.
  - Engine: two identical `win_snapshot` results (fake tool) → the second tool message is the note, and the stored step result is the full text.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** Full suite → exit 0.
- [ ] **Step 5: Commit** `perf(tokens): stable chat prefix; don't resend an unchanged screen`.

### Task A5: Superseded-state masking and the budget guard
**Files:** `context/observations.py` (the second half), `runtime/engine.py`, `settings.py`, `backend/tests/test_observations.py`, `test_engine.py`

**Produces:**
```python
# settings.py
class TokenSavingSettings(BaseModel):
    mask_superseded: bool = True   # confirmed or reverted by scripts/eval_tokens.py (see comment once run)
    mask_batch: int = Field(default=3, ge=1, le=20)
AppSettings.token_saving: TokenSavingSettings

# context/observations.py
@dataclass
class Observation:
    index: int          # position in the conversation (a tool-role message)
    tool: str
    kind: str | None    # "screen" | "page" for state; None for content (file, web page, search)
    step_no: int
    summary: str        # e.g. "fs_read of C:\\…\\question.docx" — used in stubs
    masked: bool = False

def mask_superseded(convo: list[ChatMessage], obs: list[Observation], batch: int, force: bool = False) -> int
    # the number masked. A state observation is superseded if a later one has the same kind.
    # Masks only when unmasked superseded >= batch (or force). The stub:
    # "[earlier {kind} snapshot (step {n}) omitted: a newer one is below]"
def fit_to_budget(convo: list[ChatMessage], obs: list[Observation], budget: int) -> list[str]
    # descriptions of what was masked. Order: superseded state (forced), then content observations oldest first,
    # never the newest content observation. Stub: "[{summary} (step {n}) omitted to fit; call it again if you need it]"
```

**Engine:**
- `_execute` keeps `obs: list[Observation]`, appending one when a tool message is added, with `kind = tool.observes`.
- `summary` = tool name plus the first path, URL or query argument found (`path`, `url`, `query`, `name`).
- Before each `_complete`:
  1. If `mask_superseded` is on, call `mask_superseded(convo, obs, batch)`.
  2. If `estimate(convo) > budget_for(settings, "agent", agent_max_tokens)`, call `fit_to_budget`, and log what was masked at INFO.
- The stub replaces `ChatMessage.content` in place. The `tool_call_id` is kept, so the conversation stays valid.

- [ ] **Step 1: Failing tests** (`test_observations.py`):
  - With batch 3: 3 screen observations → nothing masked (2 superseded < 3). A 4th → the first 3 are masked, and the latest stays full.
  - A content observation is never masked by `mask_superseded`.
  - `force=True` masks superseded below the batch size.
  - `fit_to_budget`: superseded first, then the oldest content, and never the newest content. It returns the descriptions and stops as soon as the conversation fits.
  - Masking twice doesn't double-stub.
  - Engine: a scripted task with 5 snapshots → the 5th call's conversation has 3 stubs and 2 full snapshots (masked in one batch). With the setting off → no stubs.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** Full suite → exit 0.
- [ ] **Step 5: Commit** `perf(tokens): mask superseded screen state; fit tasks to the model's context`.

### Task A6: The `utility` role
**Files:** `settings.py` (`default_roles()["utility"] = [RouteEntry(provider="groq", model="llama-3.1-8b-instant")]`), `providers/router.py` (`ROLE_FALLBACK = {"utility": "chat"}`: an empty or missing chain uses the fallback role's chain), `memory/extract.py` (`router.stream("utility", …)`), `frontend_app/src/features/settings/RolesSection.tsx` (a `utility` entry: label "Background", hint "Remembering facts and other housekeeping. A small fast model is plenty; empty uses the Chat models."), `scripts/eval_extract.py`, `backend/aethel/eval/extract_fixtures.json`; tests `test_router.py`, `test_extract.py`, `settings.test.tsx`

**Guard (`eval_extract.py`):**
- It loads the user's keys (`KeyStore`) and the settings.
- For each of about 20 fixtures `{previous_reply, message, known: [..], expect: [{op, text_contains | id}]}`, it runs the extractor prompt on (a) the utility chain and (b) the chat chain.
- It scores exact op agreement and prints both, writing `~/.aethel/eval/extract.json`.
- **Rule:** if utility accuracy < chat accuracy − 1 fixture, set the utility default to `[]` (so it falls back to chat), and record why in the settings comment.
- It runs only with the user's go-ahead. Until then, the comment says "not measured; falls back to chat if the key or model isn't available".

- [ ] **Step 1: Failing tests:**
  - The router: `chain("utility")` is empty in settings saved without the key → the chat chain is used (a stored-settings JSON without `utility`).
  - Extraction calls the `utility` role.
  - RolesSection renders "Background".
- [ ] **Step 2:** FAIL. **Step 3:** Implement the code and the fixtures (hand-written: 12 with ops, 8 with none). **Step 4:** Full suite, vitest → exit 0.
- [ ] **Step 5: Commit** `feat(roles): a utility role for background calls`.

### Task A7: `use_learned_skills` and `eval_tokens.py`
**Files:** `settings.py` (`use_learned_skills: bool = True`), `runtime/engine.py` (off → skip `_recall`, macro replay and `_learn`), `features/settings/LearningSection.tsx` (a switch: "Use what Aethel has learned"), `scripts/eval_tokens.py`; tests in `test_engine.py` and `settings.test.tsx`

**`eval_tokens.py`:**
- It connects to the **running** backend: the URL and token from `~/.aethel/backend.json` if present, else asked for on the command line.
- It saves the current settings, then sets `use_learned_skills=false`.
- For each arm (`mask_superseded` false, then true), for each of the 6 tasks in the spec, twice:
  1. Create a conversation and send `start_task`.
  2. Wait for a terminal `task_state`, with a 5 min timeout.
  3. Read `GET /api/tasks/{id}` for the state and usage.
- It restores the settings. It prints and writes `~/.aethel/eval/tokens.json` with, per arm: success rate, mean prompt tokens, mean calls and mean wall time.
- The tasks write under `Documents\Aethel\eval-tokens\`, and the script deletes that folder first.

- [ ] **Step 1: Failing tests:** with `use_learned_skills` off, the engine doesn't call recall, doesn't replay a compiled macro and doesn't reflect. Settings switch.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. Check how the frontend finds the backend port and token (`frontend_app/src/lib/backend.ts`) and use the same source. **Step 4:** Full suite → exit 0.
- [ ] **Step 5: Commit** `feat(eval): token A/B harness; switch for learned skills`.

### Task A8: Verify, review, PR 1
- [ ] Full pytest, `pnpm test`, `pnpm build`, `cargo test` → exit 0.
- [ ] `superpowers:requesting-code-review` over the Part A diff. Fix Critical and Important findings.
- [ ] Ask the user to run `eval_tokens.py` and `eval_extract.py` (they spend tokens). Record the results in the settings comments; if they aren't run, the PR says "not measured".
- [ ] Push `v2-phase3b`, open the PR "Phase 3b-1: token efficiency", and bind it.

---

# Part B: Web (PR 2)

## File map (Part B)
| File | Change |
|---|---|
| `backend/aethel/tools/web.py` (new) | `web_search`, `web_read`, `SourceList`, the SSRF guard |
| `backend/aethel/store/migrations/014_conversation_web.sql` | `conversations.web INTEGER` (NULL = follow global) |
| `backend/aethel/store/repos.py`, `api/routes/conversations.py` | `Conversation.web`, `PATCH {web: true|false|null}` |
| `backend/aethel/protocol.py` | `ToolActivity`, `Sources`, `Source` |
| `backend/aethel/chat/service.py`, `chat/web_loop.py` (new) | the bounded chat tool loop |
| `backend/aethel/runtime/engine.py` | web tools for tasks when the web is on for the conversation |
| `frontend_app/src/features/conversation/PromptBox.tsx`, `WebPill.tsx`, `ActivityLine.tsx`, `Citations.tsx`, `PersonaMessage.tsx`, `stores/session.ts`, `features/settings/ModeSection.tsx` | UI |

### Task B1: Web tools
**Produces:**
```python
MAX_READ_CHARS = 12_000
MAX_BODY_BYTES = 2_000_000
class BlockedAddress(Exception): ...
def check_url(url: str) -> None      # raises BlockedAddress: a non-http(s) scheme, or a host resolving to any
                                     # loopback/private/link-local/reserved/multicast/unspecified address
                                     # (ipaddress module; socket.getaddrinfo, all results)
@dataclass
class Source: n: int; title: str; url: str
class SourceList:
    def add(self, url: str, title: str) -> int      # the same URL keeps its number
    def all(self) -> list[Source]
def web_tools(sources_for: Callable[[ToolContext], SourceList], http: httpx.AsyncClient) -> list[Tool]
    # web_search {query: str, max_results?: int 1-8} tier read, untrusted results
    # web_read {url: str, start?: int >= 0} tier read, untrusted; follows redirects manually (max 5), check_url on each hop
```

**Formats:**
- `web_search` → `"[3] Title — https://…\n    snippet"`, one per result. `ddgs.DDGS().text(query, max_results=n)` runs in a thread with a 10 s `anyio.fail_after`.
- `web_read` → `"[4] Title — url\n\n" + text[start:start+12000]`, plus `"\n\n[more: call web_read with start=12000]"` when there's more.
  - Content types other than `text/html` and `text/plain` → `ToolResult(False, "That page isn't text (image/png).")`.
  - trafilatura: `extract(html, include_links=False, favor_recall=True)`, falling back to the plain text if it returns None. The title comes from `<title>`.

- [ ] **Step 1: Failing tests** (`test_web.py`; httpx `MockTransport`, and a monkeypatched `ddgs` and `getaddrinfo`):
  - Search formatting and numbering.
  - Read with paging (`start`).
  - The `[more …]` marker.
  - The redirect hop re-check: a public host redirects to `http://127.0.0.1:8765/api` → blocked.
  - Direct `http://10.0.0.5`, `http://[::1]` and `file:///c:/x` → blocked.
  - A DNS name resolving to 192.168.1.2 → blocked.
  - A 3 MB body → cut at 2 MB with no crash.
  - A non-text type → refused.
  - Source numbers are stable across a search and a read of the same URL.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** exit 0. **Step 5: Commit** `feat(web): search and read tools with an SSRF guard and numbered sources`.

### Task B2: The per-conversation toggle and events
**Produces:**
- `Conversation.web: bool | None`, and `PATCH /api/conversations/{id}` accepts `{"title"?: str, "web"?: bool | null}`.
- `def effective_web(settings: AppSettings, conv: Conversation) -> bool: return not settings.private_mode and (conv.web if conv.web is not None else settings.internet)`
- `protocol.py`:
  - `ToolActivity {message_id: str | None, task_id: str | None, kind: Literal["search","read"], label: str}`
  - `Source {n, title, url}`
  - `Sources {message_id: str | None, task_id: str | None, sources: list[Source]}`
  - Regenerate the schema and types.

- [ ] Tests: the migration, rename still works, the PATCH web tri-state, `effective_web` truth table (private overrides), and the events round-trip. **Commit** `feat(web): per-conversation web toggle and events`.

### Task B3: The chat tool loop and tasks
**Files:** `chat/web_loop.py` (new), `chat/service.py`, `runtime/engine.py`, `services.py`

**Produces:**
```python
MAX_TOOL_ROUNDS = 3
WEB_NOTE = ("You can search the web and read pages with the tools. Cite sources with [n] exactly as numbered in the "
            "results, and never invent a source or a number. If the results don't answer it, say so.")
async def run_web_turn(*, router, prompt: list[ChatMessage], tools: list[Tool], ctx: ToolContext,
                       sources: SourceList, publish, message_id: str, on_switch, budget: int) -> AsyncIterator[str]
    # yields text deltas; after MAX_TOOL_ROUNDS it streams once more with no tools and a user note
    # "Answer now from what you found."
```

**Behaviour:**
- When `effective_web` is on, `ChatService` appends `WEB_NOTE` as a system section, builds a `SourceList` and calls `run_web_turn` with purpose `web_chat`. When it's off, it streams as today.
- Each round:
  1. Stream. Text deltas pass through.
  2. On `ToolCallsReady`, publish a `ToolActivity` per call (`Searching "…"`, or `Reading bbc.co.uk` from the URL host).
  3. Run the tool handlers directly (web tools are read-tier and auto-allowed; there's no approval flow in chat).
  4. Append the assistant tool-call message and `wrap_untrusted(tool, content)` tool messages.
  5. Run `fit_to_budget` over the loop's tool messages against `budget_for(settings, "chat", max_tokens)`.
- Malformed tool arguments → a tool error message the model sees. A second malformed round ends the loop, answering with no tools.
- After the turn, if `sources.all()` isn't empty: store `meta.sources`, and publish `Sources(message_id, sources)` before `MessageEnd`.
- Stop and cancel mid-tool: the running tool task is cancelled, and the partial text is persisted as `stopped` (the existing path).
- The episodic index stores only the final user/assistant text, as today. Tool results are never extracted as facts, because the extractor only reads the user message.
- **Tasks:** `TaskEngine` gets `web_tools_for(conversation_id) -> list[Tool]`. At `_run`, when `effective_web` is on, the web tools are added to the specs offered and handled for that task only. Implement this as a per-task extra-tools list checked in `_handle` before `registry.get`.
  - Task `Sources` events carry the `task_id`, and the final task message stores `meta.sources`.

- [ ] **Step 1: Failing tests** (`test_web_chat.py`, with ScriptedProvider plus a monkeypatched `ddgs` and transport):
  - One search then an answer: the event order is `message_start`, `tool_activity`, tokens, `sources`, `message_end`, and `meta.sources` is stored.
  - Three tool rounds then a forced answer: the 4th call has no tools.
  - Web off → no tools offered, and the prompt has no `WEB_NOTE`.
  - Private mode → off even when the conversation says on.
  - Stop mid-tool → `stopped`.
  - A task with web on → `web_search` is available and runs, and `sources` arrives with the `task_id`.
  - A web tool result in a task taints it.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** Full suite → exit 0. **Step 5: Commit** `feat(web): chat answers with cited sources; tasks can search`.

### Task B4: Web UI
**Behaviour:**
- **Web pill** in the PromptBox, next to the Task pill. States are on, off and "follows Settings". A click cycles it via PATCH. It's disabled in private mode with the tooltip "Private mode keeps everything on this computer".
- **ActivityLine:** under the streaming reply, a quiet `font-sans 12.5px text-muted` line with the latest `tool_activity` label and the breathing dots. It disappears once text starts streaming or the message ends.
- **Citations** (in `PersonaMessage`, when `sources` is present):
  - The markdown text renderer turns `[n]` into `<sup><a href=url>n</a></sup>` only for n in the sources. Others stay plain text.
  - A **Sources** list under the message: `n. Title · domain`. Links open through the existing safe external-open path, the same as `open_url` / `target=_blank` with `rel=noreferrer`.
- `session.ts`: `UiMessage.sources?: Source[]` and `activity?: string`. `toUiMessages` maps `meta.sources`.
- Settings → Mode: an "Allow web access" switch for `internet`. It's disabled in private mode.

- [ ] Failing vitest tests for each; implement; `pnpm test` and `pnpm build` → exit 0. **Commit** `feat(ui): web pill, activity line and footnote citations`.

### Task B5: Verify, review, PR 2
- [ ] Suites → exit 0. Code review, then fix. Manual demo (the user): ask a current-events question with the pill on, and get footnotes that open the right pages.
- [ ] Open the PR "Phase 3b-2: web search with citations" and bind it.

---

# Part C: Background browser (PR 3)

## File map (Part C)
| File | Change |
|---|---|
| `backend/aethel/tools/browser.py` (new) | spec, adapter, snapshot parser, credential guard, element meta, idle close |
| `backend/aethel/services.py`, `settings.py` | wiring; `BrowserSettings {show: bool = False}` |
| `backend/aethel/api/routes/browser.py` (new) | `POST /api/browser/sign-in`, `DELETE /api/browser/profile`, `GET /api/browser` |
| `backend/aethel/runtime/prompts.py` | the routing rule |
| `backend/aethel/runtime/macro.py`, `runtime/engine.py` | browser steps in structure, compile and replay |
| `frontend_app/src/features/settings/BrowserSection.tsx` | UI |

### Task C1: Probe, then the adapter
- [ ] **Step 1: Probe** (scratchpad, recorded in the module docstring):
  - `npm view @playwright/mcp version` → pin the exact version in `PACKAGE`.
  - Start it with `--browser msedge --headless --isolated` through a tiny MCP client script. List its tools and their input schemas.
  - Call `browser_navigate` on `https://example.com` and save the exact result text format: the page URL line and the snapshot YAML with `[ref=eN]`.
  - Check the process list for any network endpoint that isn't browsing, e.g. telemetry. Its docs name none.
- **Produces:**
```python
PACKAGE = "@playwright/mcp@<pinned>"
SERVER = "browser"
EXPOSED: dict[str, RiskTier] = {"browser_navigate": "read", "browser_navigate_back": "read", "browser_snapshot": "read",
    "browser_wait_for": "read", "browser_tabs": "read", "browser_take_screenshot": "read", "browser_close": "read",
    "browser_click": "write", "browser_type": "write", "browser_fill_form": "write", "browser_select_option": "write",
    "browser_press_key": "write", "browser_hover": "read", "browser_handle_dialog": "write"}
@dataclass
class PageElement: ref: str; role: str; name: str; window: str   # window = page host, for ground()
def parse_page(text: str) -> tuple[str | None, list[PageElement]]   # (page url, elements) — tolerant regex parser
CREDENTIAL_RE = re.compile(r"pass(word|code)|\bpin\b|one[- ]time|\botp\b|verification code|security code", re.I)
class Browser:
    def __init__(self, settings, permissions): ...
    def adapt(self, hub, remote_tools) -> list[Tool]   # EXPOSED only; observes="page" for every tool whose result
                                                       # carries a page snapshot (navigate, back, snapshot, click, type,
                                                       # select, press_key, hover, handle_dialog, fill_form)
def browser_spec(browser: Browser, backend_port: int, llama_port: int) -> ServerSpec | None
```

**Adapter rules:**
- **Assess:**
  - `browser_navigate` with a non-http(s) URL → deny.
  - A click, type, select or press_key whose target element name (from the last parsed page, found by `ref`) matches `IRREVERSIBLE_RE` (imported from `tools/desktop.py`) → `Assessment("allow", "This looks like it sends, buys or deletes something.", target, "irreversible")`. This is the desktop behaviour.
  - `browser_type`/`fill_form` into an element whose name matches `CREDENTIAL_RE`, or whose snapshot line is marked as a password input → deny with "I never type passwords or codes. Sign in yourself (Settings → Browser → Sign in to sites)".
- **Handler:**
  1. Call the hub.
  2. Parse the result with `parse_page` and update `self._last` (url, elements).
  3. Set `ToolResult.meta = {"element": {role, name, window: host}, "app": "browser"}` for element actions (the ref resolved against the page **before** the call).
  4. Results are untrusted.
- **Idle close:** each call resets a 10-minute `asyncio` timer that calls `browser_close` through the hub when it fires. It's cancelled on hub stop.
- **Spec command:**
  - `npx -y PACKAGE --browser msedge --user-data-dir <aethel_home>/browser/profile --output-dir <aethel_home>/browser/out --blocked-origins "http://127.0.0.1:{backend};http://localhost:{backend};http://127.0.0.1:{llama};http://localhost:{llama}"`
  - Plus `--headless` unless `settings.browser.show`.
  - `None` if there's no `npx`.
  - It's added to `default_mcp_servers`.

- [ ] **Step 2: Failing tests** (`test_browser.py`, with a fake hub and `mt.Tool` lists, like `test_file_commander.py`):
  - Only `EXPOSED` tools are registered; `browser_evaluate`, `browser_run_code_unsafe` and `browser_file_upload` never are.
  - `parse_page` on the probed sample → the URL and elements with their refs.
  - Tiers and the irreversible click.
  - Typing into a "Password" field is denied, and so is an "Enter the 6-digit code" field.
  - A navigate to `file:///` is denied.
  - Meta is recorded from the pre-call page.
  - `observes == "page"`.
  - Spec args: headless is present or absent depending on the setting, and blocked origins are listed.
  - The idle timer fires `browser_close` (short timer injected).
- [ ] **Step 3:** Implement. **Step 4:** Run → exit 0. **Step 5: Commit** `feat(browser): Aethel's own Edge through Playwright MCP, vetted`.

### Task C2: Settings, sign-in, routing rule, replay pictures
**Behaviour:**
- `GET /api/browser` → `{"status": hub status of "browser", "show": bool, "profile_exists": bool}`.
- `POST /api/browser/sign-in`:
  1. Call `browser_close` if it's running.
  2. Launch Edge **headed** directly on the profile: `msedge.exe --user-data-dir=<profile> --no-first-run about:blank`, finding the exe under `%ProgramFiles(x86)%` or `%ProgramFiles%` `\Microsoft\Edge\Application\msedge.exe`.
  3. Return 202. It's 409 while a task is using the browser (a browser step in a running task).
- `DELETE /api/browser/profile`: `hub.restart("browser")` after deleting `<aethel_home>/browser/profile` (there's a confirm in the UI). It's 409 while a task runs.
- The **routing rule** is appended to `PLANNER_SYSTEM` and to the executor system: *"For web work (looking something up on a site, filling a form, checking a page) use the browser_* tools: that's your own browser in the background. Use the visible desktop browser or open_url only when the user names a browser, wants to watch or listen to something, or asks to see it."*
- **Replay pictures:** after each successful `write`-tier browser step, when `replay_thumbnails` is on, the adapter calls `browser_take_screenshot`. It reads the image content (via `hub.call_raw`), downsizes it with Pillow to ≤480 px JPEG, and returns it as `ToolResult.thumbnail`. This is the same pipeline as desktop.
- UI **Settings → Browser**:
  - Status, and "Show browser" (applies on the next start; a note says so, and a Restart button calls `POST /api/tools/browser/restart` if it exists, otherwise `hub.restart`).
  - "Sign in to sites…" and "Clear browser data" (with a confirm dialog).

- [ ] Failing tests (routes with a fake launcher and hub, the prompt contains the rule, the thumbnail path with a fake image result, vitest for the section) → implement → exit 0. **Commit** `feat(browser): sign-in, clear data, routing rule, replay pictures`.

### Task C3: Browser macros
**Changes in `runtime/macro.py`:**
```python
OBSERVE |= {"browser_snapshot", "browser_take_screenshot", "browser_wait_for", "browser_tabs"}
POINTER |= {"browser_click", "browser_hover", "browser_select_option"}
TYPE_TOOLS = {"win_type", "browser_type"}
def _replayable(step): ok and step.tool.startswith(("win_", "browser_")) and step.tool not in OBSERVE
                       and step.tool != "browser_fill_form"   # a form fill makes the run uncompilable (returns None)
```
- `compile_macro`:
  - `browser_type` needs a target, like `win_type` with a loc.
  - Typed text and `browser_navigate` URLs get the same parameter lifting (a URL uses the `{pN+}` form).
  - Recorded `ref` and `element` args are dropped (a ref belongs to one snapshot).
- `describe()` gains browser verbs ("Go to {url}", "Click “…”", "Type “…” into “…”").

**Engine `_run_macro`:**
- For a step with a target whose tool starts with `browser_`:
  1. Call the `browser_snapshot` tool.
  2. `parse_page` → elements.
  3. `ground(target, elements, …)`, which is unchanged: `PageElement` has role, name and window, where window is the host.
  4. Set `args["ref"] = element.ref` and `args["element"] = element.name`.
- A missing browser → handover "the browser isn't connected".
- Desktop steps are unchanged.

- [ ] **Step 1: Failing tests** (`test_macro.py`, `test_macro_run.py`):
  - A run of navigate(url with the goal's query), type(search box, the goal words), click(first result) compiles, with a URL param and a target `{role, name, window: host}`.
  - `fill_form` → None.
  - Replay with a fake browser: an exact match sets the ref from the fresh snapshot; a missing element → handover (drift), then the LLM loop.
  - Desktop macros are unchanged (existing tests pass).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** Full suite → exit 0. **Step 5: Commit** `feat(macros): browser steps compile and replay by role, name and host`.

### Task C4: Verify, review, PR 3
- [ ] Suites → exit 0. Code review, then fix.
- [ ] **Manual demo** (the user):
  - "Find the opening hours of the Leeds Central Library" runs in the background browser with no window, and the answer is right.
  - Run a repeatable web task 4 times and watch it compile and speed up.
  - Sign-in opens a window.
  - Clear data works.
- [ ] Open the PR "Phase 3b-3: background browser" and bind it. Update the memory file.

---

## Self-review (done while writing)
- **Token spec coverage:**
  - §3 accounting → A1, A2, A3.
  - §4.1 stable prefix → A4 (chat), and A5's batching (executor).
  - §4.2 unchanged → A4.
  - §5.1 masking and the budget guard → A5, with the chat loop in B3.
  - §5.2 utility → A6.
  - §6 deferred → not built; it's decided from A2's breakdown data.
  - §7 guard → A7 and A8.
- **Phase 3 spec coverage:**
  - §6.1 → B1. §6.2 → B2 (reusing `internet`). §6.3 → B3. §6.4 → B4.
  - §7.1 → C1. §7.2 → C1. §7.3 → C2. §7.4 → C2. §7.5 → C2. §7.6 → C3.
  - §9 events → B2.
- **Names used across tasks:** `Usage`, `StreamDone.usage`, `UsageLog.task_totals`, `Tool.observes`, `Observation`, `mask_superseded`, `fit_to_budget`, `SourceList`, `effective_web`, `PageElement`, `parse_page` are defined before use and used consistently.
