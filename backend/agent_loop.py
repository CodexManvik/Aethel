"""
agent_loop.py

Multi-step agent execution loop (RSM Phase 1).

Replaces the legacy single-shot regex tool interception with a real agentic
cycle: generate → parse tool call → execute → observe → generate again, until
the model produces a final natural-language answer or the step budget runs out.

Tool-call protocol
------------------
The model emits at most one fenced JSON block per step:

    ```json
    {"tool": "fs.read", "args": {"path": "C:/some/file.txt"}}
    ```

JSON is used instead of the legacy `[CALL_TOOL: ...]` regex tags because
arguments containing quotes, newlines, or commas (e.g. full file contents for
fs.write) survive JSON escaping but break regex capture groups. The same
schema maps 1:1 onto OpenAI-format function calls, so a future cloud-API
backend can share this dispatch layer and only swap the parsing step.

Malformed JSON is fed back to the model as an error observation so it can
self-correct on the next step (this consumes a step from the budget).

All tool execution goes through mcp_executor, so the permission manifest and
transaction log apply identically to both the legacy and agent-loop paths.
"""

import json
import re
from typing import Any, AsyncGenerator, Awaitable, Callable, Optional

from config import (
    AGENT_MAX_STEPS,
    AGENT_TOOL_RESULT_MAX_CHARS,
)
import mcp_executor
from generation import generate_stream

# ─────────────────────────────────────────────────────────────
# Tool protocol prompt — injected by prompt_builder when the
# agent loop is enabled.
# ─────────────────────────────────────────────────────────────

AGENT_TOOL_PROTOCOL = """\
[AGENT TOOLS]
You have access to local tools on the user's PC and live web search. Use them
whenever completing the user's request requires reading or changing files,
running commands, or fetching live information.

To call a tool, output a single fenced JSON code block in this exact shape:

```json
{"tool": "<tool name>", "args": {<arguments>}}
```

Available tools:
- search      args: {"query": "<search terms>"}
- fs.read     args: {"path": "<absolute file path>"}
- fs.list     args: {"path": "<absolute directory path>"}
- fs.write    args: {"path": "<absolute file path>", "content": "<COMPLETE file content, JSON-escaped>"}
- fs.delete   args: {"path": "<absolute file path>"}
- shell       args: {"command": "<shell command>"}
- fetch       args: {"url": "<http(s) URL>"}

Rules:
- At most ONE tool call per response. Output NOTHING after the JSON block.
- You may briefly state what you are about to do before the JSON block.
- After each tool result you may call another tool or answer the user.
- When you have everything you need, answer naturally WITHOUT a JSON block.
- For fs.write, "content" must be the complete file contents with newlines
  escaped as \\n — never a diff or a placeholder.
- Never run destructive shell commands (deleting, formatting, killing
  processes). File changes go through fs.write / fs.delete so they can be
  rolled back.
"""

# Fenced ```json ... ``` block extractor.
_FENCED_JSON_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL | re.IGNORECASE)

_KNOWN_TOOLS = {"search", "fs.read", "fs.list", "fs.write", "fs.delete", "shell", "fetch"}

# How many times the model may repeat an identical tool call before the loop
# stops executing it (small models are prone to getting stuck re-calling).
_MAX_IDENTICAL_CALLS = 1

# Cap on how often ONE tool may be used per turn, even with different args.
# Without this a failing search gets retried with reworded queries until the
# whole step budget is gone.
_MAX_CALLS_PER_TOOL = 2

_REQUIRED_ARGS = {
    "search": ("query",),
    "fs.read": ("path",),
    "fs.list": ("path",),
    "fs.write": ("path", "content"),
    "fs.delete": ("path",),
    "shell": ("command",),
    "fetch": ("url",),
}


class ToolCallParseError(Exception):
    """A tool call was attempted but its JSON was malformed or invalid."""


def parse_tool_call(text: str) -> Optional[dict]:
    """
    Extracts a tool call from model output.

    Returns:
        {"tool": str, "args": dict} if a valid call is present,
        None if the output contains no tool-call attempt (= final answer).
    Raises:
        ToolCallParseError if a call was clearly attempted but is invalid,
        so the loop can feed the error back to the model.
    """
    candidates: list[str] = [m.group(1).strip() for m in _FENCED_JSON_RE.finditer(text)]

    # Fallback: bare {"tool": ...} object outside a fence.
    if not candidates:
        idx = text.find('{"tool"')
        if idx == -1:
            idx = text.find("{'tool'")
        if idx != -1:
            candidates.append(text[idx:])

    if not candidates:
        return None  # no tool-call attempt → final answer

    raw = candidates[0]
    try:
        obj, _ = json.JSONDecoder().raw_decode(raw)
    except json.JSONDecodeError as e:
        raise ToolCallParseError(f"Invalid JSON in tool call block: {e}")

    if not isinstance(obj, dict):
        raise ToolCallParseError("Tool call must be a JSON object with 'tool' and 'args' keys.")

    # A ```json block that isn't shaped like a tool call (no "tool" key at all)
    # is treated as content the model chose to show, not a call.
    if "tool" not in obj:
        return None

    tool = obj.get("tool")
    args = obj.get("args", {})

    if tool not in _KNOWN_TOOLS:
        raise ToolCallParseError(
            f"Unknown tool '{tool}'. Valid tools: {', '.join(sorted(_KNOWN_TOOLS))}"
        )
    if not isinstance(args, dict):
        raise ToolCallParseError("'args' must be a JSON object.")

    missing = [a for a in _REQUIRED_ARGS[tool] if not str(args.get(a, "")).strip()]
    if missing:
        raise ToolCallParseError(
            f"Tool '{tool}' is missing required argument(s): {', '.join(missing)}"
        )

    return {"tool": tool, "args": args}


def _is_tool_call_block(block: str) -> bool:
    """True if a fenced block is a tool call (and so must not be shown to the user)."""
    inner = block.strip()
    if inner.startswith("```"):
        inner = inner[3:]
    if inner.endswith("```"):
        inner = inner[:-3]
    # Drop an optional language tag on the first line.
    first_nl = inner.find("\n")
    if first_nl != -1 and inner[:first_nl].strip().lower() in ("json", ""):
        inner = inner[first_nl + 1:]
    inner = inner.strip()
    if not inner.startswith("{"):
        return False
    try:
        obj, _ = json.JSONDecoder().raw_decode(inner)
    except json.JSONDecodeError:
        return False
    return isinstance(obj, dict) and "tool" in obj


class ToolCallStreamFilter:
    """
    Removes tool-call JSON blocks from the user-visible stream as it arrives.

    The model's tool calls are internal plumbing; showing raw JSON in chat looks
    broken. Legitimate fenced code blocks (```python, or JSON the user actually
    asked for) are passed through untouched — only blocks that parse as a tool
    call are suppressed. Handles fences split across streaming chunks.
    """

    def __init__(self) -> None:
        self._pending = ""      # text not yet safe to emit
        self._block = ""        # current fenced block being accumulated
        self._in_block = False

    def feed(self, chunk: str) -> str:
        self._pending += chunk
        out: list[str] = []
        while True:
            if not self._in_block:
                idx = self._pending.find("```")
                if idx == -1:
                    # Hold back a trailing partial fence ("`" / "``") so we
                    # don't emit half a marker.
                    hold = 2 if self._pending.endswith("``") else (1 if self._pending.endswith("`") else 0)
                    if hold:
                        out.append(self._pending[:-hold])
                        self._pending = self._pending[-hold:]
                    else:
                        out.append(self._pending)
                        self._pending = ""
                    break
                out.append(self._pending[:idx])
                self._block = "```"
                self._pending = self._pending[idx + 3:]
                self._in_block = True
            else:
                idx = self._pending.find("```")
                if idx == -1:
                    self._block += self._pending
                    self._pending = ""
                    break
                self._block += self._pending[:idx] + "```"
                self._pending = self._pending[idx + 3:]
                self._in_block = False
                if not _is_tool_call_block(self._block):
                    out.append(self._block)  # real code block → show it
                self._block = ""
        return "".join(out)

    def flush(self) -> str:
        """Emits any remaining buffered text at the end of a step."""
        tail = ""
        if self._in_block:
            # Unterminated block: if it already looks like a tool call, drop it;
            # otherwise show it so we never silently eat the model's answer.
            if not _is_tool_call_block(self._block + "```"):
                tail = self._block
            self._block = ""
            self._in_block = False
        tail += self._pending
        self._pending = ""
        return tail


def strip_tool_calls(text: str) -> str:
    """Removes tool-call JSON blocks from text (for clean history/telemetry)."""
    f = ToolCallStreamFilter()
    return (f.feed(text) + f.flush()).strip()


def _format_dir_listing(entries: Any) -> str:
    if not isinstance(entries, list):
        return str(entries)
    lines = [
        f"{'[DIR] ' if e['is_dir'] else ''}{e['name']}"
        + (f" ({e['size_bytes']} bytes)" if not e["is_dir"] else "")
        for e in entries
    ]
    return "\n".join(lines) or "(empty directory)"


async def dispatch_tool(
    tool: str,
    args: dict,
    web_search: Optional[Callable[[str], str]] = None,
) -> tuple[bool, str, str]:
    """
    Executes one tool call through the permission-gated executor layer.

    Returns (ok, result_text, status_label) where status_label is the short
    human-readable marker streamed to the UI (e.g. "Reading file: x.txt").
    """
    import asyncio

    if tool == "search":
        query = str(args["query"])
        if web_search is None:
            return False, "Web search is not available in this context.", f"Searching: {query}"
        result = await asyncio.to_thread(web_search, query)
        return True, result, f"Searching: {query}"

    if tool == "fs.read":
        path = str(args["path"])
        ok, result = await mcp_executor.execute_read_file(path)
        return ok, result, f"Reading file: {path}"

    if tool == "fs.list":
        path = str(args["path"])
        ok, result = await mcp_executor.execute_list_dir(path)
        return ok, _format_dir_listing(result) if ok else str(result), f"Listing directory: {path}"

    if tool == "fs.write":
        path = str(args["path"])
        content = str(args["content"])
        ok, result = await mcp_executor.execute_write_file(path, content)
        return ok, result, f"Writing to: {path}"

    if tool == "fs.delete":
        path = str(args["path"])
        ok, result = await mcp_executor.execute_delete_file(path)
        return ok, result, f"Deleting: {path}"

    if tool == "shell":
        command = str(args["command"])
        ok, result = await mcp_executor.execute_run_command(command)
        return ok, result, f"Running: {command}"

    if tool == "fetch":
        url = str(args["url"])
        ok, result = await mcp_executor.execute_fetch_url(url)
        return ok, result, f"Fetching: {url}"

    return False, f"Unhandled tool '{tool}'", tool


def _truncate(text: str, limit: int = AGENT_TOOL_RESULT_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more characters]"


async def run_agent_loop(
    messages: list[dict],
    target_model: str,
    web_search: Optional[Callable[[str], str]] = None,
    result_sink: Optional[dict] = None,
) -> AsyncGenerator[str, None]:
    """
    Runs the multi-step agent cycle, streaming text chunks to the caller.

    `messages` is mutated in place (assistant turns and tool observations are
    appended), matching how the legacy path builds its second pass.

    `result_sink`, if provided, receives:
        result_sink["model_text"]  — concatenated model output across steps
                                     (excluding UI status markers), suitable
                                     for history/telemetry/selfie/voice.
        result_sink["steps"]       — number of generation steps taken.
        result_sink["tool_calls"]  — list of executed {tool, args, ok} dicts.
    """
    model_text_parts: list[str] = []
    executed: list[dict] = []
    steps_taken = 0
    budget_exhausted = False
    repeat_signature: Optional[str] = None
    repeat_count = 0

    for step in range(AGENT_MAX_STEPS):
        steps_taken = step + 1
        step_text = ""
        # Tool-call JSON is internal plumbing — filter it out of what the user
        # sees while still capturing the full text for parsing.
        stream_filter = ToolCallStreamFilter()
        async for chunk in generate_stream(messages, target_model):
            step_text += chunk
            visible = stream_filter.feed(chunk)
            if visible:
                yield visible
        tail = stream_filter.flush()
        if tail:
            yield tail

        model_text_parts.append(step_text)

        try:
            call = parse_tool_call(step_text)
        except ToolCallParseError as e:
            # Feed the parse error back so the model can retry with valid JSON.
            messages.append({"role": "assistant", "content": step_text})
            messages.append({
                "role": "system",
                "content": (
                    f"[TOOL CALL ERROR]\n{e}\n"
                    "Re-emit the tool call as a single valid fenced JSON block, "
                    "or answer the user directly without a tool call."
                ),
            })
            yield "\n\n*[Retrying tool call...]*\n\n"
            continue

        if call is None:
            break  # final natural-language answer — we're done

        tool, args = call["tool"], call["args"]

        # Loop guard: small models sometimes re-issue the same call forever,
        # burning the whole budget. Nudge once, then stop calling that tool.
        signature = f"{tool}:{json.dumps(args, sort_keys=True, default=str)}"
        if signature == repeat_signature:
            repeat_count += 1
        else:
            repeat_signature, repeat_count = signature, 0

        # Per-tool budget: stop a failing tool being retried with reworded
        # arguments until the step budget is exhausted.
        tool_uses = sum(1 for c in executed if c["tool"] == tool)
        if tool_uses >= _MAX_CALLS_PER_TOOL:
            messages.append({"role": "assistant", "content": step_text})
            messages.append({
                "role": "system",
                "content": (
                    f"[TOOL BUDGET REACHED] You have already used '{tool}' "
                    f"{tool_uses} times this turn. Do not call it again. "
                    "Answer the user now with what you have, and say plainly "
                    "if the tool did not return useful information."
                ),
            })
            yield f"\n\n*[Enough {tool} attempts — answering with what I have.]*\n\n"
            continue

        if repeat_count >= _MAX_IDENTICAL_CALLS:
            messages.append({"role": "assistant", "content": step_text})
            messages.append({
                "role": "system",
                "content": (
                    f"[LOOP DETECTED] You already called {tool} with these exact "
                    "arguments and have the result above. Do NOT call it again. "
                    "Answer the user now using what you already know."
                ),
            })
            yield "\n\n*[Already have that result — wrapping up.]*\n\n"
            continue

        ok, result, label = await dispatch_tool(tool, args, web_search)
        executed.append({"tool": tool, "args": {k: str(v)[:200] for k, v in args.items()}, "ok": ok})

        yield f"\n\n*[{label}...]*\n\n"

        status = "OK" if ok else "ERROR"
        messages.append({"role": "assistant", "content": step_text})
        messages.append({
            "role": "system",
            "content": (
                f"[TOOL RESULT: {tool} ({status})]\n"
                f"{_truncate(str(result))}\n\n"
                "Continue the task. Call another tool if needed, or give the "
                "user your final answer without a JSON block."
            ),
        })
    else:
        budget_exhausted = True

    if budget_exhausted:
        # Don't leave the user with a half-finished tool spew: spend one final
        # generation on a plain-language answer with tool calling switched off.
        yield "\n\n*[Wrapping up…]*\n\n"
        messages.append({
            "role": "system",
            "content": (
                "[STEP LIMIT REACHED] No more tool calls are allowed. Using only "
                "the information gathered above, give the user your best final "
                "answer now. Do NOT output a JSON block. If the task is "
                "incomplete, say what you found and what is still needed."
            ),
        })
        final_text = ""
        final_filter = ToolCallStreamFilter()
        async for chunk in generate_stream(messages, target_model):
            final_text += chunk
            visible = final_filter.feed(chunk)
            if visible:
                yield visible
        tail = final_filter.flush()
        if tail:
            yield tail
        model_text_parts.append(final_text)

    if result_sink is not None:
        # Store the user-facing text (tool JSON stripped) so chat history and
        # telemetry stay clean and the model isn't re-fed its own raw calls.
        cleaned = [strip_tool_calls(p) for p in model_text_parts]
        result_sink["model_text"] = "\n".join(p for p in cleaned if p.strip())
        result_sink["steps"] = steps_taken
        result_sink["tool_calls"] = executed
        result_sink["budget_exhausted"] = budget_exhausted
