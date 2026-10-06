# Aethel v2: Roadmap for the Remaining Phases

- **Date:** 2026-10-06
- **State:** Phases 0–2 merged. Phase 3a (memory) is merged (PR #11). Phase 3b part 1 (token efficiency) is merged (PR #12).
- **Parent spec:** `docs/superpowers/specs/2026-09-23-aethel-v2-design.md`
- **This file:** the order of work, the PRs, the carry-overs, and the decisions to confirm. Each phase has its own detailed plan (listed below), written like the 3a/3b plans: files, interfaces, core code, test assertions, commits.

## 1. What we learned that changes the order

The token eval on the user's local model (Gemma 4B, 128k context, llama-server) measured where agent prompts go. It's recorded in `llm_calls`. Estimated prompt tokens per executor call:

| Part | Tokens |
|---|---|
| **Tool schemas** | **~9,100** |
| System prompt | ~360 |
| History | ~200 |
| Tool results | ~180 |

Notes on that run:
- llama-server's own prompt cache served almost all of each repeated prefix, e.g. 7,377 of 7,412 tokens cached. So the stable-prefix rule from 3b-1 pays off on local models.
- With hosted providers, cached or not, those ~9k schema tokens still count against per-minute limits and cost.
- The Phase 3b browser adds 14 tools. So **cutting schema cost comes before the browser**: it's the largest remaining waste, and the browser would grow it.

## 2. The order

| # | PR | Plan | What it delivers | Depends on |
|---|---|---|---|---|
| 1 | **3b-2: tool schemas** | `2026-10-06-aethel-v2-phase3b-a2-tool-schemas.md` | Per-tool schema accounting; compact schemas (lossless); tool groups on demand (`use_tools`), so a task carries only the core tools plus what it asks for; the eval-script carry-overs | 3b-1 (merged) |
| 2 | **3b-3: web** | `2026-09-30-aethel-v2-phase3b-tokens-web-browser.md`, **Part B**, with the amendments in §4 | `web_search`/`web_read` with an SSRF guard, the chat tool loop, citations, the per-conversation pill; web tools are an on-demand group for tasks | #1 |
| 3 | **3b-4: browser** | same file, **Part C**, with the amendments in §4 | Aethel's own headless Edge via Playwright MCP, as an on-demand group; sign-in; replay pictures; browser macros | #1, #2 |
| 4 | **4a: voice engine** | `2026-10-06-aethel-v2-phase4-voice.md`, **Part A** | `/ws/voice`, STT, System 1 end-of-turn, sentence-streamed Kokoro TTS with persona voice blends, barge-in with truncation, voice transcripts | #2 (the chat turn shape) |
| 5 | **4b: call view** | same file, **Part B** | Mic capture + VAD in the webview, the playback worklet, the call screen (portrait rings, captions, task strip), task narration, E3 latency harness | #4 |
| 6 | **5a: personas** | `2026-10-06-aethel-v2-phase5-personas-images.md`, **Part A** | Persona folders, the store and migration (built-in Aethel, legacy Rosia), the studio (editor, voice-blend editor), accent extraction, the tone engine port, distillation port | #4 (voice blends) |
| 7 | **5b: images** | same file, **Part B** | `ImageBackend` (Gemini image, OpenRouter, local Z-Image with VRAM handoff), the `image_generate` chat tool, the identity check (YuNet + ArcFace), the developing-photo card, gallery, portrait/sheet generation in the studio | #6, #2 (chat tool loop) |
| 8 | **6a: evaluation** | `2026-10-06-aethel-v2-phase6-polish-eval.md`, **Part A** | E1 computer-use suite with checkers and the 4 conditions; E2 grounding set from E1 runs; E3/E4/E5 harnesses; results store; the evaluation dashboard | #1–#7 |
| 9 | **6b: polish** | same file, **Part B** | First-run onboarding, the ⌘K palette, teach mode, a permissions editor, deleting the legacy backend, README and docs | #8 |

Each PR goes from its own branch off `main` after the previous one merges. The user's workflow after a merge applies: check out main, pull, delete the merged branch. **The app runs from this checkout.**

## 3. Carry-overs (must land in PR #1)
1. **The eval-script safety fixes** are only on the local `v2-phase3b` branch (`b88f3fd`, `081bc0c`): `--base-url` for both eval scripts, and window-allowlisted approvals in `eval_tokens.py`. Cherry-pick both.
2. **The Store-app window name.** Calculator and Settings report their window as `applicationframehost`. Add it to the `calc` and `display` allowlists (the edit was written but never applied when the permission checker failed). Test: `decide("calc", "win_type", "Type “1234” in applicationframehost", "write") == "allow_once"` and the same call for `haiku` is `deny`.
3. **Thinking models need room.** The local model reasons for 160+ tokens before a one-line answer.
   - `memory/extract.py` runs at `max_tokens=settings.max_tokens` (1024) on the `utility`→`chat` chain, which is enough. The eval scripts get `--max-tokens`.
   - Add `RouteEntry.reasoning: bool = False`. When true, budgets reserve `max(reply, 2048)`. Settings → Models shows it as a checkbox, "Thinks before answering".
4. **The unfinished token A/B.** It's left unrun. The schema work in PR #1 changes the baseline, so the A/B is rerun once, after PR #1, on both masking and schema groups. See the 3b-a2 plan, Task 6.

## 4. Amendments to Parts B and C (the 2026-09-30 plan), from the 3b-1 review fixes
- `fit_to_budget(convo, obs, budget, extra)` returns `list[Observation]` (not strings). Each has `.summary` and `.signature`. The chat tool loop must pop `seen[o.signature]`, as the engine does.
- Observations come only from successful real tool calls. Set `ctx.last_ok` in the chat loop's tool runner the same way `_end_step` does.
- The guard budget uses `budget_for(settings, role, reply, capped=False, entries=router.primary(role))`.
- Stubs and notes never cite step numbers.
- Web and browser tools are **groups** (`web`, `browser`) from PR #1. For tasks, `web` is offered on demand when the conversation's web toggle is on, and `browser` always on demand. Chat still offers `web_search`/`web_read` directly when the pill is on.
- The browser tools that return page snapshots set `observes="page"`.

## 5. Decisions to confirm (defaults are what the plans assume)

| # | Decision | Default in the plans | Why |
|---|---|---|---|
| D1 | Order: schemas before web | **Yes** (§1) | Largest measured waste; the browser grows it |
| D2 | Speech-to-text default | **Local faster-whisper** (already in `models/stt`, `small`), Groq `whisper-large-v3-turbo` optional | Local-first preference; Groq's limits worry the user; faster-whisper small on CPU int8 takes ~0.3–0.8 s per short utterance |
| D3 | Call-mode latency target | **Measured, not promised.** Spec target p50 ≤ 1.2 s end-of-speech → first audio is for hybrid; local LLMs report what they get | The user's chat role is a local thinking model |
| D4 | Default image backend | **Gemini image model** through Gemini's native REST API (a key is already configured for vision); OpenRouter second; local Z-Image only if weights are installed (`models/image/` is empty today) | Reference-image support is what makes faces consistent |
| D5 | Face-identity model | **ArcFace ONNX from the ONNX Model Zoo** (`arcfaceresnet100-8.onnx`), downloaded on first use; YuNet from `opencv_zoo` | Avoids InsightFace's non-commercial weights. Its license is checked and recorded in Task 5b-5 before use |
| D6 | Legacy "Rosia" persona | **Migrated** into a persona folder (spec §9); hidden unless legacy data exists | Spec decision |
| D7 | Microphone capture | **In the webview** (getUserMedia + AudioWorklet + `@ricky0123/vad-web`), with a spike first; fallback: capture in the backend with `sounddevice` | Spec §8.1; WebView2's permission prompt is the risk, hence the spike |
| D8 | E1 needs Word? | **Optional:** Word tasks run only if Office is installed (detected via COM), otherwise they're reported as "not run" | Not every machine has Office |
| D9 | Teach mode scope | **Record a demonstration into a draft skill** (steps from Windows-MCP snapshots before and after each click/keypress); no macro until it has 3 verified runs | Spec §15 Phase 6; it keeps the macro rule |

## 6. Rules carried through every phase
- **Quality first:** cuts are lossless or measured. Thresholds are measured with a dev/test split, or labelled "not measured".
- **Memory and actions are never silent:** Noted lines, approvals, replay.
- **Untrusted content stays wrapped.** Facts only ever come from user-typed chat.
- Evals that spend the user's tokens or drive the desktop run only with the user's go-ahead. While they run, nobody uses the PC, and approvals are allowlisted per task.
- Tests: `AETHEL_MCP=0`, `AETHEL_SYSTEM1=0`, fake embedder. pytest `-q` hides "passed", so check exit codes. Use the Edit/Write tools for multi-line Python (the Git-Bash heredoc escaping gotcha).
- Each PR: suites green, a review subagent ("sonnet"), fixes, then the PR is bound with the ccd_pr tools, and the memory file is updated.
