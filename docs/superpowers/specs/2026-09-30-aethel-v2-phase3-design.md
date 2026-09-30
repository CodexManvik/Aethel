# Aethel v2 Phase 3: Memory and Web

- **Date:** 2026-09-30
- **Branch:** `v2-phase3`
- **Status:** approved in brainstorming; awaiting review of this written spec
- **Parent spec:** `2026-09-23-aethel-v2-design.md` (§6.1, §6.2, §6.4, §7, §15 Phase 3)
- **Author:** Manvik Talwar, with Claude

---

## 1. Summary

Phase 3 gives Aethel three things. It remembers facts about you across conversations. It recalls earlier conversations when they're relevant. It can use the internet, both to answer questions with cited sources and to do web tasks in a browser of its own that runs in the background.

It also puts one **context builder** under both chat and the agent. That replaces the ad-hoc prompt assembly used today, which can't enforce a token budget.

This phase ships as two PRs:

| PR | Scope | Demo |
|---|---|---|
| **3a: Memory** | facts (semantic), episodic recall, context builder, Memory → Facts tab, "Noted" line with Undo, recall footer | Tell it a fact, then recall it in a new conversation |
| **3b: Web and browser** | `web_search`/`web_read` with citations, chat tool loop, background browser (Playwright MCP, Edge), browser macros | Ask a current-events question and get footnotes; run a background web task twice and watch the second run get faster |

### 1.1 Decisions (from brainstorming, 2026-09-30)

| Topic | Decision | Why |
|---|---|---|
| Scope | Phase 3 as specced in the parent spec, plus background browser control (deferred here from Phase 2) | User's call |
| Semantic memory | **Our own Mem0-style implementation**, not the `mem0ai` library | Easier to maintain, debug and upgrade. `mem0ai` 2.x brings posthog telemetry (on by default), qdrant-client/grpc and sqlalchemy. Its local embedders assume sentence-transformers, which we replaced with ONNX bge. The algorithm itself is small and gets cited as Mem0's |
| Extraction trigger | **A Laya gate, then the LLM.** System 1 decides whether a message is worth remembering; only on yes does the LLM extract | Most messages contain no fact. This saves one LLM call per turn and follows the System 1 / System 2 pattern |
| Chat and the web | **Chat gets web tools.** The model decides when to search or read, writing its own queries from context | Good queries for follow-up questions, and it can read pages. The provider layer already streams tool calls. Private mode forces web off, so local tool-call reliability never matters |
| Background browser | **Aethel's own Edge profile** through `@playwright/mcp`, headless by default, persistent profile | Your real browsers and logins stay untouched. Edge is on every Windows PC, so there's no browser download |
| Browser routing | **Background by default.** Visible only when you name a browser, the task is video or music, or you ask to see it | Web work shouldn't take over your screen |
| Web default | Global web toggle **off** by default | Queries go to a third party (DuckDuckGo) |

---

## 2. Semantic memory: facts (3a)

### 2.1 Storage (migration `010_facts.sql`)

```sql
CREATE TABLE facts (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL,                 -- 'user' | 'persona:<persona_id>'
  text TEXT NOT NULL,
  vector BLOB NOT NULL,                -- bge-small float32[384], L2-normalised
  source_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
  conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_facts_scope ON facts(scope);

CREATE TABLE fact_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fact_id TEXT NOT NULL,               -- kept after the fact is deleted
  op TEXT NOT NULL CHECK (op IN ('add', 'update', 'delete')),
  old_text TEXT,
  new_text TEXT,
  actor TEXT NOT NULL CHECK (actor IN ('extractor', 'user')),
  source_message_id TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_fact_events_fact ON fact_events(fact_id);
```

- **Scopes:**
  - `user` holds facts about the user and is shared by every persona.
  - `persona:<id>` holds relationship facts, such as "we have a running joke about X".
  - Only `persona:aethel` exists until Phase 5.
- **`fact_events`** is append-only. It answers "how did this fact get here, and what did it say before?" Both the UI history drawer and debugging read it.
- **Search** is brute-force cosine in numpy over the rows in scope, with vectors cached in memory and invalidated on writes. A few thousand facts takes microseconds, so there's no vector database.

### 2.2 `memory/facts.py`: `FactStore`

- `add(scope, text, source_message_id, conversation_id, actor) -> Fact`
- `update(fact_id, text, actor, source_message_id=None) -> Fact | None`
- `delete(fact_id, actor) -> bool`, which is a hard delete of the row; the event stays.
- `search(query, scopes, k, min_score) -> list[(Fact, score)]`
- `list(scope=None, q=None) -> list[Fact]`
- `history(fact_id) -> list[FactEvent]`

Each write and its event are committed in one transaction. Blocking calls run in a thread (`anyio.to_thread`), as the rest of the store does.

### 2.3 `memory/extract.py`: the pipeline

It runs **after the assistant reply has been persisted**, as a background task. It's serialised per conversation (the same lock pattern as `ChatService._turn`) and never delays the reply.

1. **Skip** when fact extraction is off, the turn came from a task route, or the reply ended in `error`.
2. **Gate (System 1).** `system1.ask({"message": user_text, "previous_reply": last_assistant_text}, {"fact": {"type": "noul", "instructions": FACT_Q}}, "fact_gate")`. Continue only if `noul >= settings.memory.fact_threshold`.
   - The threshold and the wording of `FACT_Q` are measured on `eval/s1_fact.jsonl` (§8), using the intent-router method: tune on the even rows, report on the odd rows.
   - If System 1 is unavailable (still loading, or it failed), **skip**. This is deliberate: a missed fact costs less than an extra LLM call on every turn while Laya loads.
3. **Neighbours.** `search(user_text, scopes=["user", f"persona:{pid}"], k=5, min_score=0.0)`.
4. **One LLM call** (`chat` role; local in private mode). The prompt contains the user message, the previous reply (for resolving "yes, that one"), and the neighbours with short ids (`f1`…`f5`). It asks for strict JSON:
   ```json
   {"ops": [
     {"op": "add", "scope": "user", "text": "Lives in Leeds"},
     {"op": "update", "id": "f2", "text": "Works at Path Infotech as a backend developer"},
     {"op": "delete", "id": "f4"}
   ]}
   ```
   - An empty `ops` list means nothing to change (Mem0's NOOP).
   - Facts are short third-person statements about the user, or about the relationship for persona scope.
   - Mem0 uses two calls, extraction and then a decision. Here they're merged, because the neighbours are already known from the message.
5. **Apply.**
   - Ids are validated against the neighbour map, and unknown ids are dropped.
   - Texts are capped at 300 characters.
   - At most 5 operations per turn are applied.
   - Invalid JSON gets one retry with the parse error appended; after that the turn is skipped and logged.
6. **Emit** `FactsChanged(conversation_id, message_id, changes=[{fact_id, op, text, old_text}])`. The UI shows this as the "Noted" line (§5.2).

**Trust rule.** Facts come only from **user-typed chat messages**. Web results, tool output, file contents and task steps never reach the extractor. This closes the path where a web page or document plants a memory. The model sees recalled facts as data (§4.3), never as instructions.

### 2.4 Settings

`AppSettings.memory`:
- `facts_enabled: bool = True`
- `fact_threshold: float` (measured)
- `episodic_enabled: bool = True`
- `episodic_min_score: float` (measured)
- `facts_k: int = 6`
- `episodes_k: int = 3`

---

## 3. Episodic memory (3a)

### 3.1 `memory/episodic.py`: `EpisodicIndex`

- **Unit:** one **exchange**, meaning a user message plus the assistant reply that followed it. The text is `"User: …\nAethel: …"`, truncated to 512 tokens at embedding time (bge's limit).
- **Index:** a turbovec `IdMapIndex` (dim 384) at `~/.aethel/episodic/index.tv`. It's written with `write()` after each batch and loaded with `load()` at startup. If the file is missing or won't load, the index is rebuilt from SQLite and a warning is logged, never a crash.
- **Map (migration `011_episodes.sql`):**
  ```sql
  CREATE TABLE episodes (
    id INTEGER PRIMARY KEY,              -- the turbovec id
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_message_id TEXT NOT NULL,
    assistant_message_id TEXT NOT NULL UNIQUE,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
  );
  ```
- **Indexing:**
  - Incremental: after a reply finishes with `complete` status, the exchange is embedded and added.
  - At startup, a catch-up pass indexes any `complete` exchanges that have no `episodes` row.
  - When a conversation is deleted, its ids are removed from the index (`remove`) and the rows cascade.
- **Search:** `search(query, exclude_conversation_window: set[message_id], k, min_score)`.
  - Exchanges already in the current prompt window are excluded.
  - Candidates below `min_score` (measured, §8) are dropped.
  - Ties within 0.02 go to the more recent exchange.
  - **No cross-encoder reranker.** The legacy one brings back torch. Whether a reranker earns its cost is left to E5.
- Importing a persona's corpus (chat exports) waits for Phase 5 and will use a separate index.

---

## 4. Context builder (3a)

### 4.1 `context/builder.py`

```python
@dataclass
class Section:
    key: str            # "persona" | "task" | "window" | "facts" | "episodes" | "skills" | "notes"
    priority: int       # lower = dropped first
    text: str
    ids: list[str]      # fact/episode/skill ids included, for attribution
    shrinkable: bool    # window: trimmed oldest-first instead of dropped whole

@dataclass
class BuiltContext:
    messages: list[ChatMessage]
    included: dict[str, list[str]]   # {"facts": [...], "episodes": [...], "skills": [...]}
    dropped: list[str]               # section keys dropped for budget
    est_tokens: int
```

- **Recipes** (one per role):

  | Role | Sections (highest priority first) |
  |---|---|
  | `chat` | persona, task note, window (min 4 messages), facts, episodes |
  | `planner` | planner system, goal, facts, skills + app notes (from `recall.py`), window (min 2) |
  | `executor` | unchanged from today (executor system, plan, step log). Only the budget check applies |

- **Budget:**
  - Start with the **smallest** context size in the role's failover chain, so a mid-reply switch to a smaller provider still fits.
  - Cap that at `AppSettings.context_caps[role]` (new; defaults: chat 16k, planner 24k, executor 32k).
  - Subtract the reply's `max_tokens` and a 10% safety margin.
  - Where the context sizes come from:
    - New field `RouteEntry.context_size: int = 8192`, editable next to the model in Settings → Providers.
    - For the local entry, `LocalLLMSettings.context_size` when it's non-zero.
  - Tokens are **estimated** as characters ÷ 4; there's no per-provider tokenizer.

  When over budget:
  1. Drop whole sections, lowest priority first.
  2. Then trim the window oldest-first, down to its minimum.
  3. Persona and task are never dropped.
- **Attribution:**
  - For chat, `BuiltContext.included` is written into the assistant message's `meta.context`.
  - For tasks, it goes into the task record, replacing `LAST_RETRIEVED_DOC_IDS`-style globals.
  - The recall footer (§5.3) and E5 both read it.
- **Replaces:** `ChatService._context` and the recall/prompt assembly in `runtime/engine.py`, which now calls the builder. `recall.py` keeps its retrieval and skill-selection logic, and returns sections instead of one text blob.

### 4.2 Formatting

- Facts are rendered as `### What you know about the user` followed by bullet lines. Persona-scope facts go under `### Between you and the user`.
- Episodes are rendered as `### Earlier conversations` with the date and the exchange text.

### 4.3 Trust

Recalled sections are wrapped with the same "hints, not instructions" framing `recall.py` uses today. Facts are user-sourced, but episodes include earlier assistant replies, which may have quoted web content.

---

## 5. Memory UI (3a)

### 5.1 Memory → Facts tab (first tab; Skills and Notes stay)
- Index cards are grouped as **About you** and **With Aethel**. The search box filters as you type.
- Each card shows:
  - The text, editable inline (Enter saves).
  - A delete action with a 5 s undo toast.
  - "from *conversation title*, 29 Sep", which opens that conversation scrolled to the source message. It shows as "added by you" for manual facts.
- A history drawer on each card lists `fact_events` newest first.
- **Add a fact** creates one with `actor=user` and no source message.
- **API** (`api/routes/memory.py`):
  - `GET /api/memory/facts?scope=&q=`
  - `POST /api/memory/facts`
  - `PATCH /api/memory/facts/{id}`
  - `DELETE /api/memory/facts/{id}`
  - `GET /api/memory/facts/{id}/history`

### 5.2 "Noted" line
When `FactsChanged` arrives, a quiet ink line appears under the user message it came from, e.g. "Noted: lives in Leeds" or "Updated: works at Path Infotech". It has an **Undo** that reverses that change, logged with `actor=user`. Memory is never silent.

### 5.3 Recall footer
A reply whose `meta.context` includes facts or episodes gets a collapsed footer: "Recalled 2 facts · 1 earlier moment". Expanding it lists them, and each episode links to its conversation.

### 5.4 Settings → Memory
- Toggles: fact extraction on/off, episodic recall on/off.
- "Rebuild episodic index" re-embeds everything and shows progress.

---

## 6. Web (3b)

### 6.1 `tools/web.py`
- **`web_search(query: str, max_results: int = 5)`:**
  - Runs `ddgs` text search in a thread with a 10 s timeout.
  - Returns numbered entries: `[n] Title, URL` followed by the snippet.
  - Risk tier: read (auto).
  - `untrusted=True`.
- **`web_read(url: str)`:**
  - Fetches with httpx under these limits:
    - 15 s timeout.
    - At most 5 redirects, each re-checked.
    - Body capped at 2 MB (streamed and cut off).
    - Only `text/html` and `text/plain` are accepted.
  - Main text is extracted with `trafilatura` and capped at 32 000 characters.
  - Returns `[n] Title, URL` followed by the text.
  - Risk tier: read (auto).
  - `untrusted=True`.
- **SSRF guard:**
  - Before each request, including every redirect hop, the host is resolved and rejected if any address is loopback, private, link-local, reserved or multicast. That blocks our API on :8765, llama-server, and the LAN.
  - Only `http` and `https` are allowed.
- **Sources:** each turn (chat) or task has one `SourceList`. A URL keeps the same number when it's seen again; `web_search` results and `web_read` pages share the list. It's saved to `meta.sources = [{n, title, url}]` and sent in a `Sources(message_id | task_id, sources)` event.

### 6.2 Toggles
- `AppSettings.web.enabled: bool = False` (global).
- Migration `012_conversation_web.sql`: `ALTER TABLE conversations ADD COLUMN web INTEGER` (NULL means follow global, 0 off, 1 on).
- **Effective web** = not private mode AND (the conversation value if set, otherwise the global value).
- The prompt-box pill shows and sets the per-conversation value. It's disabled, with a tooltip, in private mode.

### 6.3 Chat tool loop (`chat/service.py`)
When effective web is on, the chat stream is offered `web_search` and `web_read`:

1. Stream. Text deltas stream to the UI as today.
2. On `ToolCallsReady`, publish `ToolActivity(message_id, kind, label)`, e.g. "Searching 'uk inflation september 2026'" or "Reading bbc.co.uk".
3. Run the tools, append the assistant tool-call message and the tool results (wrapped as untrusted), then stream again.
4. After **3 tool rounds**, stream one last time with no tools and an instruction to answer from what was found.

- The system prompt gains a web note: cite with `[n]` exactly as numbered, and never invent a source.
- Tool results enter context through the builder's window (they count against the budget).
- Stop and cancel work mid-tool: the tool task is cancelled, and the partial text is persisted as `stopped`.
- Tasks get the same two tools through the tool registry whenever effective web is on for the conversation that started the task.

### 6.4 Citations UI
- `[n]` markers in the text become superscript links.
- A **Sources** list under the message shows the favicon-less title, the domain and the full URL. Opening one goes to the system browser, with the same safe-open path as `open_url`.
- A marker whose number isn't in the list is rendered as plain text, never as a link.

---

## 7. Background browser (3b)

### 7.1 Server
- `tools/browser.py` provides `browser_spec(...) -> ServerSpec | None`, following the `file_commander.py` pattern:
  - The package is `@playwright/mcp@<pinned exact version>`, run through `npx -y`. The version is pinned when the plan is written; if `npx` is missing, the spec is `None`.
  - Arguments:
    - `--browser msedge`
    - `--user-data-dir ~/.aethel/browser/profile`
    - `--output-dir ~/.aethel/browser/out`
    - `--headless`, unless Settings → Browser → "Show browser" is on
    - `--blocked-origins` listing the backend and llama-server origins
  - No `--caps`, so the vision, pdf and devtools groups stay off.
  - Telemetry: none is documented. The plan checks the package for network calls other than browsing before pinning it.
- **Exposed subset** (renamed with the `browser_` prefix kept):
  - `navigate`, `navigate_back`, `snapshot`, `click`, `type`, `fill_form`, `select_option`, `press_key`, `hover`, `wait_for`, `tabs`, `take_screenshot`, `handle_dialog`, `close`.
  - **Excluded:**
    - `browser_evaluate` and `browser_run_code_unsafe` (arbitrary JS).
    - `browser_file_upload` (would read local files into a website).
    - `browser_drag`/`drop`, which can be added later if a task needs them.
    - Network, storage and console tools.
- **Lifecycle:**
  - The server starts on the **first browser tool call**, not at boot.
  - It's closed after 10 idle minutes.
  - The npx process sits in the backend's Job Object, so the kill switch (Ctrl+Alt+Esc) and backend exit take Edge down with it.

### 7.2 Risk and trust
- Every page snapshot and result is `untrusted` and taints the task.
- `navigate`, `snapshot`, `wait_for`, `tabs` and `take_screenshot` are read tier. `navigate` to a non-http(s) scheme is refused.
- `click`, `type`, `fill_form`, `select_option`, `press_key` and `handle_dialog` go through the **same assessor as desktop control**: the target's accessible name is checked against the consequential-action list (send, pay, buy, delete, submit, confirm…), which raises the tier and asks you.
- Typing into a credential field is refused. A field counts as one when its accessible name matches password, passcode, PIN, OTP or one-time code, or when the snapshot marks it as a password input. The agent never enters credentials; you sign in yourself (§7.4).

### 7.3 Routing
- A rule in the planner prompt: *Use the background browser for web work (look something up on a site, fill a form, check a page). Use the visible desktop browser, or `open_url`, only when the user names a browser, the task is to watch or listen to something, or the user asks to see it.*
- Skills record `apps: [browser]` (background) or the desktop browser's name, so a learned skill replays in the same place.

### 7.4 Settings → Browser
- **Show browser** toggle (headed mode). It takes effect on the next start of the server.
- **Sign in to sites…** launches the profile headed on a blank page so you log in yourself, then closes it.
- **Clear browser data** stops the server and deletes `~/.aethel/browser/profile` after you confirm.

### 7.5 Replay
Every browser step saves a screenshot as its replay thumbnail. It's taken through the server's screenshot tool into `~/.aethel/browser/out` and moved into the task's replay folder, following the existing replay-thumbnail setting.

### 7.6 Browser macros
Phase 2b macros address desktop elements as `{role, name, window}`. Browser steps compile the same way with a different target:

- **Target:** `{role, name, host}`, taken from the snapshot line the step acted on. Playwright's `ref` values (`e12`) belong to a single snapshot and are **never stored**.
- **Grounding at replay:**
  1. Take a fresh snapshot.
  2. Exact match on role + name among elements on the same host.
  3. Otherwise, a Laya `choice` among same-role candidates plus "none".
  4. "none", or a confidence below the grounding threshold, counts as **drift**, which hands control to the LLM, followed by repair. This is the same path as desktop macros.
- **Parameters:** URL-aware lifting (`{p1+}`) already exists for `open_url`, and `browser_navigate` URLs use it too.
- **Compilation** uses the unchanged rule: 3 same-structure verified successes.

---

## 8. Measurement

Following the honesty rule, every threshold is either measured or labelled "not measured".

| Item | Data | Method | Output |
|---|---|---|---|
| Fact gate threshold and wording | `backend/aethel/eval/s1_fact.jsonl`, about 120 messages labelled `fact`/`no_fact` (small talk, commands, questions, opinions, stated facts, corrections, hypotheticals) | `scripts/eval_s1_fact.py`: tune on even rows, report P/R on odd rows | `fact_threshold` default and the reported held-out P/R |
| Episodic similarity floor | about 40 (query → relevant exchange / none) pairs from a scripted multi-conversation fixture | `scripts/eval_episodic.py` | `episodic_min_score` default |
| Extraction quality | fixture conversations with expected ops | pytest with a recorded LLM response, plus an optional live run behind an env flag | pass/fail; live results are reported, not asserted |

E5 (Mem0 on vs off, recall accuracy) stays in Phase 6. The `meta.context` attribution recorded here is its data source.

---

## 9. Events (protocol additions)

| Event | Fields | When |
|---|---|---|
| `FactsChanged` | `conversation_id`, `message_id`, `changes[{fact_id, op, text, old_text}]` | after extraction applies operations |
| `ToolActivity` | `message_id`, `kind` (`search`/`read`), `label` | chat tool loop, before each tool runs |
| `Sources` | `message_id` or `task_id`, `sources[{n, title, url}]` | whenever the source list grows |

Types are generated through the existing codegen (`events.schema.json` to `events.gen.ts`).

---

## 10. Testing

- **pytest:**
  - **Extraction:** all four operations, unknown-id drop, the 5-operation cap, JSON retry, System 1 unavailable, and the trust rule (task and tool text never extracted). Uses a fake LLM and a fake System 1.
  - **FactStore:** transactions, history, cosine search, scope filtering.
  - **Episodic:** incremental add, catch-up, rebuild on a corrupt index, conversation-delete removal, window exclusion.
  - **Builder:** priority dropping, window trimming to minimum, never dropping persona or task, `included` ids.
  - **Web:** `web_read` against httpx `MockTransport` (content-type, size cap, redirect re-check, SSRF on direct IPs and on resolved hosts), and `web_search` with ddgs mocked.
  - **Chat tool loop:** single round, 3-round cap, stop mid-tool, source numbering stable across tools.
  - **Browser adapter:** subset filtering, tiers, password-field refusal, macro target extraction and grounding, all with a fake MCP session.
  - **Integration:** a live Playwright-MCP test behind `AETHEL_BROWSER_IT=1`, with default runs keeping `AETHEL_MCP=0`.
- **vitest:** Facts tab (edit, delete, undo, history), Noted line with Undo, recall footer, web pill states (including private mode), `ToolActivity` line, footnotes and source list, unknown-marker rendering.
- **Manual demos:** the two in §1, with a real Groq key and the real Laya and bge.

---

## 11. Out of scope

- Behaviour rules (legacy RSM `rules/`) aren't ported. The builder recipe keeps a slot for them.
- Persona corpus import and per-persona scopes other than `aethel` wait for Phase 5.
- A cross-encoder reranker (left to E5).
- Driving the user's real browser or profile.
- The E5 experiment itself (Phase 6).

## 12. Risks

| Risk | Mitigation |
|---|---|
| Laya is weak zero-shot on a new question, so the fact gate misses facts | Measured on a held-out split; wording tuned the way the intent gate was; a manual "Add a fact" is always available; a low P/R result is reported honestly and could motivate fine-tuning (dissertation angle already noted) |
| The extraction LLM writes junk or duplicate facts | Neighbours are given for dedupe, texts are capped, operations are capped, and the Noted line with Undo keeps every change visible |
| The ddgs package breaks (it scrapes) | Wrapped behind `web_search`; failures come back as tool errors the model can report; the provider could later be swapped in one place |
| Groq tool-call quirks in chat | Malformed tool calls come back as tool errors for one retry; after that the loop answers without tools |
| Playwright MCP API churn | Pinned exact version; an adapter layer; the subset is checked against the live tool list at startup and missing tools are logged |
| Edge headless differs from headed (site blocks) | The "Show browser" toggle; app notes record per-site quirks |
