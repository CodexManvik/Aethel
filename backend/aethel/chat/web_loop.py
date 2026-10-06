"""Chat answers that can search the web and cite what they found (Phase 3 spec §6).

A bounded tool loop: the model may search and read for up to MAX_TOOL_ROUNDS rounds, then it is asked once more,
without tools, to answer from what it found. Web tools are read-tier, so there is no approval step in chat; their
results are wrapped as untrusted data. Only text the model writes is yielded."""
import json
from contextlib import aclosing
from typing import AsyncIterator, Awaitable, Callable

from ..context.observations import Observation, fit_to_budget
from ..protocol import ToolActivity
from ..providers.base import ChatMessage, StreamDone, TextDelta, ToolCall, ToolCallsReady, ToolSpec
from ..providers.router import ProviderSwitch, RoleRouter
from ..safety.untrusted import wrap_untrusted
from ..settings import AppSettings
from ..store.repos import Conversation
from ..tools.base import Tool, ToolContext, ToolResult
from ..tools.web import host_of, unlisted_read
from ..usage import estimate_breakdown

MAX_TOOL_ROUNDS = 3
WEB_NOTE = ("You can search the web and read pages with the tools. Cite sources with [n] exactly as numbered in the "
            "results, and never invent a source or a number. If the results don't answer it, say so.")
ANSWER_NOW = "Answer now from what you found."
KINDS = {"web_search": "search", "web_read": "read"}


def effective_web(settings: AppSettings, conv: Conversation) -> bool:
    """Whether web tools may be offered in this conversation. Private mode wins over everything; otherwise the
    conversation's own switch, and where it has none, Settings → Allow web access."""
    return not settings.private_mode and (conv.web if conv.web is not None else settings.internet)


def _args(raw: str) -> dict | None:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def activity_label(name: str, args: dict) -> str:
    if name == "web_read":
        return f"Reading {host_of(str(args.get('url') or '')) or 'a page'}"
    if name == "web_search":
        query = " ".join(str(args.get("query") or "").split())
        return f'Searching "{query[:60]}"'
    return name


def _what(call: ToolCall, args: dict) -> str:
    """What a result was, for the stub that may replace it: 'web_read of https://…'."""
    target = next((str(args[k]) for k in ("url", "query", "path") if args.get(k)), "")
    return f"{call.name} of {target[:120]}" if target else call.name


async def run_web_turn(*, router: RoleRouter, prompt: list[ChatMessage], tools: list[Tool], ctx: ToolContext,
                       sources, publish: Callable[[object], Awaitable[None]], message_id: str,
                       on_switch: Callable[[ProviderSwitch], Awaitable[None]], budget: int) -> AsyncIterator[str]:
    """Yields the reply's text. `sources` is the turn's numbered source list (the tools add to it through ctx).
    `budget` is how many prompt tokens the model about to be called can take (RoleRouter.primary's real limit)."""
    ctx.sources = sources
    convo = list(prompt)
    specs = [ToolSpec(t.name, t.description, t.parameters) for t in tools]
    schema_tokens = sum(estimate_breakdown([], specs).values())
    by_name = {t.name: t for t in tools}
    observed: list[Observation] = []
    malformed = 0
    spoke = False

    for round_no in range(MAX_TOOL_ROUNDS + 1):
        final = round_no == MAX_TOOL_ROUNDS or malformed >= 2
        if final:
            convo.append(ChatMessage("user", ANSWER_NOW))
        calls: list[ToolCall] = []
        text: list[str] = []
        stream = router.stream("chat", convo, tools=None if final else specs, on_switch=on_switch,
                               purpose="web_chat", ref={"message_id": message_id})
        async with aclosing(stream):
            async for ev in stream:
                if isinstance(ev, TextDelta):
                    if spoke and not text:
                        yield "\n\n"  # a new paragraph after the words that came before the tools
                    text.append(ev.text)
                    yield ev.text
                elif isinstance(ev, ToolCallsReady):
                    calls.extend(ev.calls)
                elif isinstance(ev, StreamDone):
                    pass
        spoke = spoke or bool("".join(text).strip())
        if final or not calls:
            return

        convo.append(ChatMessage("assistant", "".join(text), tool_calls=calls))
        round_malformed = False
        for call in calls:
            args = _args(call.arguments)
            tool = by_name.get(call.name)
            ctx.last_ok = False
            if args is None:
                round_malformed = True
                result = ToolResult(False, f"Error: the arguments for {call.name} were not a JSON object. Try again.")
            elif tool is None:
                result = ToolResult(False, f"Error: there's no tool called {call.name!r}. "
                                           f"Tools: {', '.join(by_name)}.")
            elif unlisted_read(call.name, args, ctx):
                result = ToolResult(False, "Error: I can only open pages that came up in a search, or that you gave me, "
                                           "once I've read outside content. Search again, or ask the user for the address.")
            else:
                if call.name in KINDS:
                    await publish(ToolActivity(message_id=message_id, task_id=None, kind=KINDS[call.name],
                                               label=activity_label(call.name, args)))
                try:
                    result = await tool.handler(args, ctx)
                except Exception as exc:  # a tool that crashes is an error the model can read, not a dead reply
                    result = ToolResult(False, f"Error: {exc}")
            ctx.last_ok = result.ok  # only what a tool really returned counts as an observation
            if result.untrusted:
                ctx.tainted = True  # outside content has been read: see unlisted_read
            convo.append(ChatMessage("tool", wrap_untrusted(call.name, result.content) if result.untrusted
                                     else result.content, tool_call_id=call.id))
            if tool is not None and args is not None and ctx.last_ok:
                observed.append(Observation(len(convo) - 1, call.name, None, _what(call, args), call.name +
                                            json.dumps(args, sort_keys=True)))
        malformed = malformed + 1 if round_malformed else 0
        fit_to_budget(convo, observed, budget, extra=schema_tokens)
