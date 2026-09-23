"""The task engine: plan → act → verify (spec §4.1).

One asyncio runner per task. State is persisted after every change, so a
restart leaves unfinished tasks 'paused' and resumable."""
import asyncio
import json
import logging
import time
from collections import Counter
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from ..chat.service import make_title
from ..hub import EventHub
from ..protocol import (CheckOutcome, ConversationUpdated, ErrorEvent, PlanProgress, ProviderSwitched,
                        StepFinished, StepStarted, TaskCreated, TaskPlan, TaskState, VerificationResult)
from ..providers.base import ChatMessage, ProviderError, TextDelta, ToolCall, ToolCallsReady, ToolSpec
from ..providers.router import NoProviderAvailable, ProviderSwitch, RoleRouter
from ..safety.approvals import ApprovalBroker
from ..safety.policy import decide
from ..store.repos import ConversationRepo, MessageRepo
from ..tools.base import ToolContext, ToolResult
from ..tools.registry import ToolRegistry
from .checks import Check, CheckResult, run_checks
from .prompts import COMPLETE_STEP, FINISH_TASK, PLANNER_SYSTEM, SUBMIT_PLAN, executor_system, repair_prompt, resume_note
from .store import TERMINAL_STATES, TaskRepo

log = logging.getLogger("aethel.tasks")
MAX_TOOL_RESULT_CHARS = 12_000
MAX_IDENTICAL_CALLS = 2


class PlanningError(Exception):
    pass


@dataclass
class Outcome:
    summary: str | None
    budget_exhausted: bool


def _parse_args(raw: str) -> dict | None:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _parse_plan(raw: str) -> tuple[list[str], list[Check]] | None:
    args = _parse_args(raw)
    if args is None:
        return None
    steps = [str(s).strip() for s in args.get("steps") or [] if str(s).strip()][:10]
    if not steps:
        return None
    checks = []
    for item in args.get("checks") or []:
        try:
            checks.append(Check.model_validate(item))
        except ValidationError:
            continue  # drop unusable checks rather than rejecting the plan
    return steps, checks


def _first_line(text: str) -> str:
    return (text.strip().splitlines() or [""])[0][:200]


class TaskEngine:
    def __init__(self, *, tasks: TaskRepo, messages: MessageRepo, conversations: ConversationRepo,
                 router: RoleRouter, registry: ToolRegistry, approvals: ApprovalBroker, hub: EventHub,
                 max_steps: int = 40, max_seconds: float = 15 * 60, clock=time.monotonic):
        self.tasks = tasks
        self.messages = messages
        self.conversations = conversations
        self.router = router
        self.registry = registry
        self.approvals = approvals
        self.hub = hub
        self.max_steps = max_steps
        self.max_seconds = max_seconds
        self.clock = clock
        self._runners: dict[str, asyncio.Task] = {}
        self._gates: dict[str, asyncio.Event] = {}  # set = may proceed, clear = paused
        self._cancel_requested: set[str] = set()

    # ---- public API --------------------------------------------------------
    async def start(self, *, conversation_id: str, goal: str, client_id: str | None = None) -> str | None:
        conv = self.conversations.get(conversation_id)
        if conv is None:
            await self.hub.publish(ErrorEvent(message="Conversation not found.", code="bad_request"))
            return None
        task = self.tasks.create(conversation_id, goal)
        user_msg = self.messages.add(conversation_id, "user", goal, meta={"task_id": task.id})
        await self.hub.publish(TaskCreated(task_id=task.id, conversation_id=conversation_id, goal=goal,
                                           user_message_id=user_msg.id, client_id=client_id))
        if not conv.title:
            title = make_title(goal)
            self.conversations.rename(conv.id, title)
            await self.hub.publish(ConversationUpdated(conversation_id=conv.id, title=title))
        self._launch(task.id, note=None)
        return task.id

    async def pause(self, task_id: str) -> bool:
        gate = self._gates.get(task_id)
        if gate is None or not gate.is_set():
            return False
        gate.clear()
        await self._set_state(task_id, "paused")
        return True

    async def resume(self, task_id: str) -> bool:
        gate = self._gates.get(task_id)
        if gate is not None:
            if gate.is_set():
                return False
            gate.set()
            await self._set_state(task_id, "running")
            return True
        record = self.tasks.get(task_id)
        if record is None or record.state != "paused":
            return False
        self._launch(task_id, note=resume_note(self.tasks.steps(task_id)))
        return True

    async def cancel(self, task_id: str) -> bool:
        runner = self._runners.get(task_id)
        if runner is not None:
            self._cancel_requested.add(task_id)
            gate = self._gates.get(task_id)
            if gate is not None:
                gate.set()  # a paused runner must wake up to be cancelled
            runner.cancel()
            return True
        record = self.tasks.get(task_id)
        if record is None or record.state != "paused":
            return False
        await self._finish(task_id, "cancelled", None)
        return True

    def note_for_chat(self, conversation_id: str) -> str | None:
        active = [t for t in self.tasks.list_for_conversation(conversation_id) if t.state not in TERMINAL_STATES]
        if not active:
            return None
        listed = "; ".join(f'"{t.goal}" ({t.state.replace("_", " ")})' for t in active)
        return (f"You are also working on a task for the user in the background: {listed}. If they ask, tell them "
                "how it is going; they can pause or cancel it from the task panel.")

    async def wait_idle(self) -> None:
        while self._runners:
            await asyncio.gather(*list(self._runners.values()), return_exceptions=True)

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Stop every runner now; interrupted tasks are left 'paused'."""
        runners = list(self._runners.values())
        for runner in runners:
            runner.cancel()
        if runners:
            await asyncio.wait(runners, timeout=timeout)

    # ---- runner --------------------------------------------------------------
    def _launch(self, task_id: str, note: str | None) -> None:
        gate = asyncio.Event()
        gate.set()
        self._gates[task_id] = gate
        runner = asyncio.create_task(self._run(task_id, note))
        self._runners[task_id] = runner

        def cleanup(_: asyncio.Task) -> None:
            self._runners.pop(task_id, None)
            self._gates.pop(task_id, None)
            self._cancel_requested.discard(task_id)

        runner.add_done_callback(cleanup)

    async def _run(self, task_id: str, note: str | None) -> None:
        started = self.clock()
        try:
            record = self.tasks.get(task_id)
            if not record.plan:
                await self._set_state(task_id, "planning")
                steps, checks = await self._plan(task_id, record.goal)
                self.tasks.set_plan(task_id, steps, [c.model_dump() for c in checks])
                await self.hub.publish(TaskPlan(task_id=task_id, steps=steps, checks=[c.describe() for c in checks]))
                record = self.tasks.get(task_id)
            checks = [Check.model_validate(c) for c in record.checks]
            await self._set_state(task_id, "running")
            convo = [
                ChatMessage("system", executor_system(record.goal, record.plan, [c.describe() for c in checks],
                                                      datetime.now().astimezone())),
                ChatMessage("user", record.goal),
            ]
            if note:
                convo.append(ChatMessage("user", note))
            ctx = ToolContext(task_id=task_id)
            grants: set[str] = set()
            outcome = await self._execute(task_id, convo, ctx, grants, started)
            if outcome.budget_exhausted:
                await self._finish(task_id, "failed", outcome.summary,
                                   error="I ran out of steps or time before finishing.")
                return
            if checks:
                failed = [r for r in await self._verify(task_id, checks) if not r.passed]
                if failed:
                    convo.append(ChatMessage("user", repair_prompt([f"{r.description} ({r.detail})" for r in failed])))
                    await self._set_state(task_id, "running")
                    retry = await self._execute(task_id, convo, ctx, grants, started)
                    outcome = Outcome(retry.summary or outcome.summary, retry.budget_exhausted)
                    failed = [r for r in await self._verify(task_id, checks) if not r.passed]
                if failed:
                    await self._finish(task_id, "failed", outcome.summary,
                                       error="Not all checks passed: " + "; ".join(r.description for r in failed))
                    return
            await self._finish(task_id, "done", outcome.summary)
        except asyncio.CancelledError:
            if task_id in self._cancel_requested:
                await self._finish(task_id, "cancelled", None)
                return
            self.tasks.set_state(task_id, "paused")  # shutdown: resumable after the next launch
            raise
        except (NoProviderAvailable, ProviderError, PlanningError) as exc:
            await self._finish(task_id, "failed", None, error=str(exc))
        except Exception:
            log.exception("task %s crashed", task_id)
            await self._finish(task_id, "failed", None, error="something went wrong while working on this")

    async def _plan(self, task_id: str, goal: str) -> tuple[list[str], list[Check]]:
        tools = "\n".join(f"- {s.name}: {s.description}" for s in self.registry.specs())
        messages = [ChatMessage("system", PLANNER_SYSTEM),
                    ChatMessage("user", f"Goal: {goal}\n\nTools I can use:\n{tools}")]
        for _ in range(2):
            calls, text = await self._complete(task_id, messages, [SUBMIT_PLAN])
            call = next((c for c in calls if c.name == "submit_plan"), None)
            parsed = _parse_plan(call.arguments) if call is not None else None
            if parsed is not None:
                return parsed
            messages.append(ChatMessage("assistant", text, tool_calls=calls or None))
            for c in calls:
                messages.append(ChatMessage("tool", "That plan wasn't usable.", tool_call_id=c.id))
            messages.append(ChatMessage("user", "Call submit_plan with 2-10 steps and a list of checks (may be empty)."))
        raise PlanningError("I couldn't come up with a workable plan for this.")

    async def _complete(self, task_id: str, messages: list[ChatMessage],
                        tools: list[ToolSpec] | None) -> tuple[list[ToolCall], str]:
        async def on_switch(sw: ProviderSwitch) -> None:
            await self.hub.publish(ProviderSwitched(role=sw.role, from_provider=sw.from_label,
                                                    to_provider=sw.to_label, reason=sw.reason, task_id=task_id))

        calls: list[ToolCall] = []
        text: list[str] = []
        stream = self.router.stream("agent", messages, tools=tools, on_switch=on_switch)
        async with aclosing(stream):
            async for event in stream:
                if isinstance(event, TextDelta):
                    text.append(event.text)
                elif isinstance(event, ToolCallsReady):
                    calls.extend(event.calls)
        return calls, "".join(text)

    async def _execute(self, task_id: str, convo: list[ChatMessage], ctx: ToolContext, grants: set[str],
                       started: float) -> Outcome:
        specs = self.registry.specs() + [COMPLETE_STEP, FINISH_TASK]
        seen: Counter = Counter()
        while True:
            if len(self.tasks.steps(task_id)) >= self.max_steps or self.clock() - started > self.max_seconds:
                return Outcome(await self._final_summary(task_id, convo), True)
            await self._gate(task_id)
            calls, text = await self._complete(task_id, convo, specs)
            convo.append(ChatMessage("assistant", text, tool_calls=calls or None))
            if not calls:
                return Outcome(text.strip() or None, False)
            finished: str | None = None
            for call in calls:
                result = await self._handle(task_id, call, ctx, grants, seen)
                convo.append(ChatMessage("tool", result, tool_call_id=call.id))
                if call.name == "finish_task":
                    args = _parse_args(call.arguments) or {}
                    finished = str(args.get("summary") or text.strip() or "Done.")
            if finished is not None:
                return Outcome(finished, False)

    async def _handle(self, task_id: str, call: ToolCall, ctx: ToolContext, grants: set[str],
                      seen: Counter) -> str:
        args = _parse_args(call.arguments)
        if args is None:
            return f"Error: the arguments for {call.name} were not a JSON object. Try again."
        if call.name == "finish_task":
            return "Finishing up."
        if call.name == "complete_plan_step":
            index = args.get("index")
            if isinstance(index, int) and self.tasks.mark_plan_step(task_id, index):
                await self.hub.publish(PlanProgress(task_id=task_id, index=index))
                return "Noted."
            return "Error: there's no plan step with that index."
        tool = self.registry.get(call.name)
        if tool is None:
            return f"Error: there's no tool called {call.name!r}. Tools: {', '.join(self.registry.names())}."
        signature = call.name + json.dumps(args, sort_keys=True)
        seen[signature] += 1
        if seen[signature] > MAX_IDENTICAL_CALLS:
            return "[LOOP DETECTED] You've already made this exact call. Use the earlier result or try something different."

        verdict = decide(tool, tool.assess(args), tainted=ctx.tainted, grants=grants)
        step = self.tasks.add_step(task_id, tool.name, args, verdict.target, verdict.verdict)
        await self.hub.publish(StepStarted(task_id=task_id, step_id=step.id, tool=tool.name,
                                           summary=verdict.target, verdict=verdict.verdict))
        if verdict.verdict == "deny":
            return await self._end_step(task_id, step.id, tool.name, ToolResult(False, f"Denied: {verdict.reason}"), 0, ctx)
        if verdict.verdict == "ask":
            await self._set_state(task_id, "waiting_approval")
            decision = await self.approvals.request(task_id=task_id, step_id=step.id, tool=tool.name,
                                                    summary=verdict.target, reason=verdict.reason, tier=tool.tier)
            if self._gates.get(task_id) is None or self._gates[task_id].is_set():
                await self._set_state(task_id, "running")
            if decision == "deny":
                return await self._end_step(task_id, step.id, tool.name, ToolResult(
                    False, "The user declined this action. Don't try it again; find another way or finish and explain."),
                    0, ctx)
            if decision == "allow_task":
                grants.add(tool.name)
        await self._gate(task_id)
        t0 = self.clock()
        try:
            result = await tool.handler(args, ctx)
        except Exception as exc:
            log.warning("tool %s failed: %s", tool.name, exc)
            result = ToolResult(False, f"Error: {exc}")
        return await self._end_step(task_id, step.id, tool.name, result, int((self.clock() - t0) * 1000), ctx)

    async def _end_step(self, task_id: str, step_id: str, tool_name: str, result: ToolResult, duration_ms: int,
                        ctx: ToolContext) -> str:
        self.tasks.finish_step(step_id, result.ok, result.content[:4000], duration_ms)
        await self.hub.publish(StepFinished(task_id=task_id, step_id=step_id, ok=result.ok,
                                            detail=_first_line(result.content), duration_ms=duration_ms))
        content = result.content
        if len(content) > MAX_TOOL_RESULT_CHARS:
            content = content[:MAX_TOOL_RESULT_CHARS] + "\n[truncated]"
        if result.untrusted:
            ctx.tainted = True
            return f'<untrusted source="{tool_name}">\n{content}\n</untrusted>'
        return content

    async def _verify(self, task_id: str, checks: list[Check]) -> list[CheckResult]:
        await self._set_state(task_id, "verifying")
        results = run_checks(checks)
        await self.hub.publish(VerificationResult(task_id=task_id, results=[
            CheckOutcome(description=r.description, passed=r.passed, detail=r.detail) for r in results]))
        return results

    async def _final_summary(self, task_id: str, convo: list[ChatMessage]) -> str | None:
        convo.append(ChatMessage("user", "[STEP LIMIT REACHED] Stop using tools. In 1-3 sentences, tell the user "
                                         "what you did and what is left."))
        _, text = await self._complete(task_id, convo, None)
        return text.strip() or None

    async def _gate(self, task_id: str) -> None:
        gate = self._gates.get(task_id)
        if gate is not None:
            await gate.wait()

    async def _set_state(self, task_id: str, state: str) -> None:
        self.tasks.set_state(task_id, state)
        record = self.tasks.get(task_id)
        await self.hub.publish(TaskState(task_id=task_id, conversation_id=record.conversation_id, state=state))

    async def _finish(self, task_id: str, state: str, summary: str | None, error: str | None = None) -> None:
        record = self.tasks.get(task_id)
        if state == "done":
            text = summary or "Done."
        elif state == "cancelled":
            text = "Okay, I've stopped that task."
        else:
            text = (summary + "\n\n" if summary else "") + f"I couldn't finish this: {error}"
        msg = self.messages.add(record.conversation_id, "assistant", text,
                                meta={"task_id": task_id, "task_state": state})
        self.tasks.set_state(task_id, state, summary=summary, error=error)
        await self.hub.publish(TaskState(task_id=task_id, conversation_id=record.conversation_id, state=state,
                                         summary=summary, error=error, message_id=msg.id, message_text=text))
