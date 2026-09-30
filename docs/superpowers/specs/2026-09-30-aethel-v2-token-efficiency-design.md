# Aethel v2: Token Efficiency

- **Date:** 2026-09-30
- **Status:** approved in brainstorming; awaiting review of this written spec
- **Ships as:** the first of three PRs in Phase 3b (tokens → web → browser); see the 3b plan
- **Author:** Manvik Talwar, with Claude

## 1. Goal and principle

Aethel should do the same work with fewer wasted tokens: "better work with less token cost", as in the step from one model generation to the next. **Quality comes first.** Tasks must keep working and chat must keep its meaning. A cut is either lossless, or it's measured before it becomes the default.

It helps on four fronts:
- free-tier limits (Groq and Gemini cap tokens per minute and per day, and per model)
- speed (fewer input tokens means a faster first token)
- cost
- the dissertation (E1 reports LLM tokens per task, and "learning cuts tokens" is part of the USP)

## 2. Where tokens go today (measured and read from the code, 2026-09-30)

- **Nothing is recorded.** No provider usage is captured anywhere.
- **The task executor resends the whole conversation on every step,** including every earlier tool result, each capped at 12,000 characters.
  - The user's history (5 tasks) has a median of 6 LLM calls per task, which resend about 5× the tool-result tokens a single pass needs.
  - Screen snapshots are the big results. The step log stores only their first 4,000 characters, so the real sizes are larger.
- **Every agent call sends every tool schema** (Windows-MCP, Office, Desktop Commander, fs, shell). The size isn't known yet.
- **Chat** sends the persona, memory sections and 24 messages of history, which is small. The minute-level time sits in the system prompt.
- **Background calls** (fact extraction) run on the main chat model, which uses up that model's per-model quota.

## 3. Measure: `llm_calls` accounting

- **Providers:**
  - The OpenAI-compatible client sends `stream_options: {"include_usage": true}`. Groq, OpenRouter, Gemini's OpenAI endpoint and llama-server all accept it.
  - The final usage chunk becomes `StreamDone.usage = Usage(prompt, completion, cached)`, where `cached` comes from `prompt_tokens_details.cached_tokens` when reported.
  - If an endpoint rejects `stream_options`, the request is retried once without it for that provider, and a flag is remembered for the session.
- **Router:** `RoleRouter.stream(..., purpose: str, ref: dict | None)` records one row per call when the stream ends, whether it succeeds, fails or is cancelled. Migration `013_llm_calls.sql`:
  ```sql
  CREATE TABLE llm_calls (
    id TEXT PRIMARY KEY, role TEXT NOT NULL, purpose TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
    task_id TEXT, message_id TEXT,
    prompt_tokens INTEGER, completion_tokens INTEGER, cached_tokens INTEGER,
    estimated INTEGER NOT NULL DEFAULT 0,      -- 1 when the provider reported no usage (chars/4 estimate)
    breakdown TEXT NOT NULL DEFAULT '{}',      -- estimated prompt tokens by part: system, tools, history, observations
    status TEXT NOT NULL, latency_ms INTEGER, created_at TEXT NOT NULL
  );
  CREATE INDEX idx_llm_calls_task ON llm_calls(task_id);
  CREATE INDEX idx_llm_calls_created ON llm_calls(created_at);
  ```
- **Purposes:** `chat_reply`, `fact_extract`, `plan`, `execute`, `final_summary`, `reflect`, `vision_locate`, and in 3b `web_chat`.
- **The breakdown** is always estimated from characters. It's there to show which part dominates, especially tool schemas against observations, and to decide the deferred tool-subset idea (§6).
- **API:**
  - `GET /api/usage?days=7` returns totals by purpose and by day.
  - `GET /api/tasks/{id}` gains `usage: {prompt, completion, cached, calls}`.
- **UI:**
  - The task panel footer shows "12.4k in · 0.9k out · 7 calls" (with "≈" when estimated).
  - Settings → **Usage** shows the last 7 days as a table by purpose, with prompt, completion and cached tokens and calls.

## 4. Lossless cuts

1. **A stable prefix for provider caching.**
   - Within a task, every executor call already shares its prefix (system, tools and earlier turns). Provider-side implicit caching (Gemini 2.5, OpenRouter, some Groq models) can reuse it only if earlier messages don't change.
   - Rule: **earlier messages are only rewritten in batches** (§5.1), never on every call.
   - Chat: the time moves to the end of the system prompt, after the memory sections, so the persona and memory text come first. The benefit for chat is small, because its prompts are short. It's done because it's free.
2. **An unchanged state is not resent.** When a state observation (§5.1) is identical to the latest one of the same kind, the model gets `[unchanged since step N]` instead of the full text.

## 5. Guarded cuts (checked before they become the default)

### 5.1 Superseded-state masking (task executor and the 3b chat tool loop)

- Tools declare what they observe: `Tool.observes: Literal["screen", "page"] | None`.
  - `win_snapshot` → `screen`.
  - In 3b, the browser tools that return a page snapshot → `page`.
  - Content tools (file reads, `web_read`, `web_search`) have no `observes` and are **never masked for staleness**.
- A state observation is **superseded** once a newer one of the same kind exists; the newest is the truth. Superseded ones are replaced by a stub: `[earlier screen snapshot (step 4) omitted: a newer one is below]`.
- **Batching, for the cache:** masking runs only when 3 or more superseded observations are still unmasked. It masks all of them at once, so the prefix changes at most once every 3 snapshots.
- **Budget guard (a last resort, not the norm):** before each executor call, the conversation estimate is checked against `budget_for(settings, "agent", agent_max_tokens)`. If it's over:
  1. Mask superseded state even below the batch size.
  2. Then mask the oldest content observations, oldest first, with a stub that says how to get the content again (`[fs_read of C:\…\question.docx (step 2) omitted to fit; read it again if you need it]`). The newest content observation is never masked.

  Today an over-budget conversation just fails at the provider.
- The setting `AppSettings.token_saving.mask_superseded: bool = True`. This is supported by common practice: computer-use agents keep only recent screenshots, and Lindenbauer et al. 2025, "The Complexity Trap", found plain observation masking matched LLM summarisation at about half the cost (**to verify before citing**).
- It's **confirmed by §7**: if success drops, the default is switched off and the result recorded.

### 5.2 A `utility` role for background calls

- There's a new role, `utility`, for fact extraction and any later background housekeeping. Its default chain is Groq `llama-3.1-8b-instant`.
- **An empty or missing `utility` chain falls back to the `chat` chain.** Settings saved before this change have no `utility` key, so old installs behave exactly as before until the role is set in Settings → Models.
- Why: Groq's free-tier limits are per model, so background work stops competing with replies and tasks. A smaller model is also faster and cheaper.
- Reflection (writing skills) stays on `agent`: quality matters there.
- **Guard:** `scripts/eval_extract.py` runs the extraction fixtures (about 20 messages with expected operations) on the utility model and on the chat model.
  - The utility model becomes the default for extraction only if it matches the chat model's op accuracy within 1 fixture.
  - Otherwise the default utility chain is left empty (falls back to chat), and the result is recorded.

## 6. Deferred (decided by the numbers from §3)

- **Sending only relevant tool groups** (e.g. no Office schemas for a browser task). If the model can't see a tool, the task breaks, so this is only worth its risk if the breakdown shows tool schemas dominate agent prompts. It gets revisited once real `llm_calls` data exists.
- **Summarising long chat history.** Chat prompts are small today, and episodic recall already covers older context.

## 7. The quality guard: `scripts/eval_tokens.py`

- It drives the **running app** (the user's desktop, the user's key) through the API and WebSocket.
  - It runs a fixed set of 6 short desktop tasks (Notepad haiku, Calculator sum to a file, Explorer new folder, Edge open a URL, Notepad open and edit a file, Settings open Display), with masking **off** and then **on**.
  - It repeats each twice, and never runs with learned macros, so both arms do the full LLM loop. The new setting `AppSettings.use_learned_skills: bool = True` (off = no skill recall, no macro replay, no reflection) is switched off for the run and restored afterwards. E1's "LLM only" condition needs the same switch.
- It reports, per arm:
  - success (verification passed)
  - LLM calls and prompt/completion tokens from `llm_calls`
  - wall time
- Output goes to `~/.aethel/eval/tokens.json`.
- It spends the user's tokens, so it's always run by the user, or with their go-ahead. The PR records the numbers, or "not measured" if it wasn't run.

## 8. Out of scope
- Changing models' `max_tokens`, or anything that shortens replies.
- Summarisation by an LLM, which spends tokens to save tokens.
- Cost in currency: token counts only, since provider prices change.
