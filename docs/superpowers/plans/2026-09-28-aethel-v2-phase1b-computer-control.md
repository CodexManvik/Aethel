# Aethel v2 · Phase 1b (Computer Control): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Execution note (2026-09-28):** the user asked for inline execution, so this plan is compact. It gives exact files, interfaces and test assertions for each task, and each task's commit holds the code.

**Goal:** Let a task drive any Windows app (Windows-MCP) and Office (in-house COM server), within the Phase 1a safety model, with a global kill switch.

**Architecture:** `tools/mcp_hub.py` owns one stdio `ClientSession` per MCP server. Each server has an *adapter* module that allowlists that server's tools and turns them into ordinary `Tool`s (tier, assessor, grant scope), so the engine and policy stay unaware of MCP. If a task is cancelled while a tool call is in flight, that server restarts, which stops any input injection still running. The kill switch cancels every task.

**Tech Stack:** `mcp` 1.27 (client + FastMCP), `windows-mcp==0.8.6` via `uvx` (brings its own Python 3.14), pywin32 311 COM, psutil, Tauri `tauri-plugin-global-shortcut` 2.

**Spec:** `docs/superpowers/specs/2026-09-23-aethel-v2-design.md` §4.2, §4.3, §15 (Phase 1). The rulings come from `.superpowers/sdd/2026-09-23-aethel-v2-phase1a-agent-runtime/progress.md` lines 88–96.

## Global Constraints
- Every Phase 1a Global Constraint still applies: `py -3.11`, foreground commands, tool-name regex, risk tiers, untrusted wrapping, and protocol regen after every protocol change.
- **Windows-MCP is allowlisted, never passed through.** Its `PowerShell`, `FileSystem`, `Registry`, `Process`, `Scrape`, `Notification` and `Screenshot` tools bypass the permission manifest and are never registered.
- Windows-MCP runs with `ANONYMIZED_TELEMETRY=false`, pinned to `0.8.6`.
- Everything a desktop or Office tool returns is untrusted.
- Anything whose element or shortcut looks irreversible (send, submit, pay, delete, install, close) is tier `irreversible`, so it always asks.
- Office saves go through `check_path(..., "write")` and `ChangeLog.record_before_write`, so Undo covers them.

---

### Task 0: F2(c) residual bypass (done: 9dbeabb, a3924ae)
cmd-only `%…%` expansion, `, ; =` argument splitting, interpreter detection on the expanded or versioned program word.

### Task 1: MCP interface hooks
**Files:** `tools/base.py`, `tools/registry.py`, `safety/policy.py`, `runtime/engine.py`, `runtime/store.py`, `store/migrations/005_step_untrusted.sql`; tests in `test_policy`/`test_engine`/`test_tools`.
**Produces:**
- `Assessment.tier: RiskTier | None = None`: a per-call tier that can only *raise* the tool's tier.
- `Assessor` may be async (the engine awaits it when it returns an awaitable).
- `Tool.group: str | None`: grants are keyed on `(tool.group or tool.name, scope)`, so one "Allow for this task" covers every desktop action in one app.
- `decide()` returns the effective tier on the Assessment, and the approval card shows that tier.
- `ToolRegistry.unregister(name)`.
- `steps.untrusted` column: `finish_step(..., untrusted)`. On resume, a task is tainted if any step has `ok and untrusted`, which replaces the hard-coded `UNTRUSTED_TOOLS`.

**Tests:**
- a `write` tool whose assessment says `irreversible` asks even with a task grant
- an async assessor is awaited
- two tools in one group share a grant
- after resume, a task that ran an untrusted MCP-style tool is still tainted
- `unregister` removes the tool from `specs()`

### Task 2: MCP hub
**Files:** `tools/mcp_hub.py`, `tests/fake_mcp_server.py` (FastMCP: `echo`, `fail`, `slow`, `pid`, `secret`), `tests/test_mcp_hub.py`, `services.py`, `app.py` lifespan.
**Produces:**
- `ServerSpec(name, command: list[str], env: dict, adapt: Callable[[McpHub, list[mcp.types.Tool]], list[Tool]])`
- `McpHub(registry)`, with:
  - `start(specs)`: background connect, never blocks startup
  - `call(server, tool, args) -> ToolResult`: text parts joined, images noted, `isError` → `ok=False`, `untrusted=True`
  - `restart(server)`
  - `stop()`
  - `status() -> dict[str, str]`
- If a call is cancelled, the hub schedules `restart(server)` and re-raises.
- A server's tools are unregistered while it's down.

**Tests (against the real stdio fake server):**
- only adapted tools are registered, with a prefix
- echo round-trip
- `isError` → not ok
- cancelling `slow` restarts the server (a different `pid`) and re-registers its tools
- `stop()` unregisters

### Task 3: Windows desktop adapter
**Files:** `tools/desktop.py`, `tests/test_desktop.py`.
**Produces:**
- `desktop_spec() -> ServerSpec | None`: `None` when `uvx` is missing.
- Exposed tools: `win_app`, `win_snapshot`, `win_click`, `win_type`, `win_scroll`, `win_move`, `win_shortcut`, `win_wait`, `win_wait_for`, `win_clipboard`, `win_multi_select`, `win_multi_edit`, `win_displays`.
- `parse_snapshot(text) -> list[Element(x, y, role, name, window)]`: the last Snapshot is remembered, so a `label` or `loc` resolves to a named element.
- `app_at(x, y)` / `foreground_app()` (pywin32 + psutil) give the grant scope. The group is `desktop`.
- Assessors:
  - `launch_executable` asks.
  - Launching shells or interpreters (cmd, powershell, pwsh, terminal, wt, regedit, python, run) asks.
  - A `win+…` shortcut asks.
  - Clicking an element named like send/submit/pay/buy/order/delete/remove/install/uninstall/sign out, or `alt+f4`/`ctrl+w`, is `irreversible`.

**Tests:**
- the parser on a sample Snapshot
- a label that resolves to "Send" gives `irreversible`
- launching notepad → allow; launching cmd → ask
- `win+r` → ask
- the excluded tools are never adapted
- the grant scope is the app name

### Task 4: Office COM server
**Files:** `mcp_servers/__init__.py`, `mcp_servers/office.py` (FastMCP, pywin32, `Visible=True`), `tools/office.py` (adapter), `tests/test_office.py`.
**Server tools:**
- Word: `word_new`, `word_open(path)`, `word_type(text, style?)` (visible, chunked typing), `word_read`, `word_save(path)`
- Excel: `excel_open(path?)`, `excel_read(range, sheet?)`, `excel_write(range, values)`, `excel_save(path)`
- PowerPoint: `ppt_new`, `ppt_add_slide(title, body)`, `ppt_save(path)`

**Adapter:**
- `*_open` checks the path for `read`.
- `*_save` checks the path for `write`, and its handler records the ChangeLog entry *before* the save.
- `*_read` is tier `read`. Every other tool is tier `write`, group `office`, and scoped to the app.

**Tests:**
- the stdio tool list has the names above (no COM needed)
- `word_save` to a forbidden path → deny
- a save inside the allowed folder records a change before calling the hub (fake hub)

### Task 5: Kill switch and job-object shell kill
**Files:** `protocol.py` (`KillSwitch` client message), `api/ws.py`, `runtime/engine.py` (`cancel_all()`), `tools/shell.py` (per-command Job Object; kill = `TerminateJobObject`), `providers/job_object.py` (`new_job()`, `assign_to(job, pid)`, `terminate(job)`), `src-tauri` (global-shortcut plugin: Ctrl+Alt+Esc → emit `kill-switch`), `frontend_app/src/lib/killSwitch.ts` (listen → WS send).
**Tests:**
- `cancel_all` cancels two running tasks
- the WS `kill_switch` message reaches the engine
- a shell command that starts a detached grandchild (`cmd /c start /b ping -n 30 127.0.0.1`) leaves no `ping.exe` after cancel
- vitest: a `kill-switch` event sends `{type:"kill_switch"}`

### Task 6: Runtime leftovers
**Files:** `runtime/engine.py`, `hub.py`, `safety/permissions.py`.
- A text-only turn gets one nudge ("call finish_task or keep going"). A second text-only turn is accepted as the answer.
- EventHub: per-subscriber bounded queue (256) with its own sender task. On overflow the subscriber is dropped and logged.
- `.env.*` variants are protected like `.env`.
- A bare-word argument that names an existing file in the home folder is checked as a path (`type _netrc`).

**Tests:** one for each bullet.

### Task 7: Frontend
**Files:**
- `ApprovalCard` verb map: desktop and Office tools, plus a generic fallback
- task panel footer hint "Ctrl+Alt+Esc stops everything"
- Settings → Tools: MCP server status (`GET /api/tools/status`)

**Tests:** vitest for the verb fallback and the status list.

### Task 8: Vision fallback
**Files:** `providers/base.py` (`ChatMessage.images: list[str]`, data URLs), `providers/openai_compat.py` (content parts), `tools/desktop.py` (`win_locate(description)`: Windows-MCP `Screenshot` → `vision` role → `x,y`).
**Tests:**
- the openai_compat payload carries `image_url` parts
- `win_locate` parses `"123,456"` from a fake router
- `win_locate` is only registered while the desktop server is up

### Task 9: Docs and demo
- README gets a "Computer control" section.
- Close the carry-over items.
- Run every automated check.
- Manual demo on the user's machine: **"Open Notepad, write a haiku about rain and save it to my Desktop"**, then the Word essay task. Both need an approval and a working kill switch.

## Deferred (with reasons)
- **argv-exact command allowlisting.** The denylist plus hard rules cover the known bypasses, and exact-argv matching would break the v1 manifest format.
- **Playwright browser server.** Windows-MCP reaches browsers through UIA, and CDP control arrives with web tools in Phase 3.
- **Parallel read-only calls.** Latency isn't the bottleneck yet.
