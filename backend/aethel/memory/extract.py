"""Fact extraction (Phase 3 spec §2.3): System 1 decides whether a message is worth remembering; only then
does one LLM call turn it into add/update/delete operations against the nearest facts Aethel already has
(Mem0's two calls, merged into one because the neighbours are known up front). Facts only ever come
from what the user typed in chat: never from task goals, tools, files or the web (the trust rule)."""
import asyncio
import json
import logging
import re

import anyio

from ..protocol import FactChange, FactsChanged
from ..providers.base import ChatMessage, TextDelta
from ..safety.untrusted import wrap_untrusted
from .facts import FactStore, valid_scope

log = logging.getLogger("aethel.memory")
MAX_OPS = 5
NEIGHBOURS = 5
PREVIOUS_REPLY_CHARS = 600
# Chosen by scripts/eval_s1_fact.py from 4 wordings on the dev half; the longer, more specific ones did worse
# (Laya zero-shot prefers short questions, as the 2a intent work found).
FACT_Q = "Is the user telling Aethel something about themselves?"

SYSTEM = """You maintain the short facts that Aethel, an AI companion, remembers about the user.
Given the user's latest message (and Aethel's reply before it, for context), decide what to change.
Rules:
- Only use what the USER states about themselves or about their relationship with Aethel. Never infer or guess.
- A fact is one short third-person statement, e.g. "Lives in Leeds", "Has a dog called Pip".
- If the message changes or contradicts a known fact, update that fact by its id instead of adding a new one.
- If the user says a known fact is wrong or no longer true, delete it.
- scope is "user" for facts about the user, "persona" for things between the user and Aethel.
- If nothing is worth remembering, return {"ops": []}.
Answer with JSON only, for example:
{"ops": [{"op": "add", "scope": "user", "text": "..."}, {"op": "update", "id": "f1", "text": "..."}, {"op": "delete", "id": "f2"}]}"""


def gate_state(user_text: str, previous_reply: str | None) -> dict:
    state = {"message": user_text}
    if previous_reply:
        state["previous_reply"] = previous_reply[:PREVIOUS_REPLY_CHARS]
    return state


def _parse(raw: str) -> list | None:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        ops = json.loads(m.group(0))["ops"] if m else None
    except (ValueError, KeyError, TypeError):
        return None
    return ops if isinstance(ops, list) else None


class FactExtractor:
    def __init__(self, *, facts: FactStore, messages, router, system1, settings, hub):
        self.facts = facts
        self.messages = messages
        self.router = router
        self.system1 = system1
        self.settings = settings
        self.hub = hub
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    def schedule(self, *, conversation_id: str, persona_id: str, user_message_id: str) -> None:
        """Runs in the background after a reply, one at a time per conversation, so a correction
        made in the next message sees the fact this one added."""
        async def guarded() -> None:
            lock = self._locks.setdefault(conversation_id, asyncio.Lock())
            async with lock:
                try:
                    await self.run(conversation_id=conversation_id, persona_id=persona_id,
                                   user_message_id=user_message_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("fact extraction failed")

        task = asyncio.create_task(guarded())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def shutdown(self, timeout: float = 10.0) -> None:
        try:
            async with asyncio.timeout(timeout):
                await self.wait_idle()
        except TimeoutError:
            for task in list(self._tasks):
                task.cancel()
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _previous_reply(self, conversation_id: str, user_message_id: str) -> str | None:
        history = self.messages.list(conversation_id)
        idx = next((i for i, m in enumerate(history) if m.id == user_message_id), 0)
        before = [m for m in history[:idx] if m.role == "assistant" and m.content]
        return before[-1].content if before else None

    async def _complete(self, messages: list[ChatMessage], message_id: str | None = None) -> str:
        parts = []
        async for ev in self.router.stream("chat", messages, temperature=0.1, purpose="fact_extract",
                                           ref={"message_id": message_id} if message_id else None):
            if isinstance(ev, TextDelta):
                parts.append(ev.text)
        return "".join(parts)

    async def run(self, *, conversation_id: str, persona_id: str, user_message_id: str) -> list[FactChange]:
        s = self.settings.get().memory
        user = self.messages.get(user_message_id)
        if not s.facts_enabled or user is None or user.role != "user" or user.meta.get("task_id"):
            return []
        previous = self._previous_reply(conversation_id, user_message_id)
        answer = await self.system1.ask(gate_state(user.content, previous),
                                        {"fact": {"type": "noul", "instructions": FACT_Q}}, "fact_gate")
        # No System 1 (still loading, off, failed): skip. A missed fact costs less than an LLM call every turn.
        if answer is None or answer["fact"]["noul"] < s.fact_threshold:
            return []
        persona_scope = f"persona:{persona_id}"
        # embedding blocks: never on the event loop
        near = await anyio.to_thread.run_sync(
            lambda: self.facts.search(user.content, ["user", persona_scope], k=NEIGHBOURS))
        ids = {f"f{i}": fact for i, (fact, _) in enumerate(near, 1)}
        known = "\n".join(f"{key}: {fact.text}" for key, fact in ids.items()) or "(none yet)"
        prompt = f"Known facts:\n{known}\n\n"
        if previous:  # context only: a reply may quote web pages or files, so it's data, not instructions
            prompt += ("Aethel's previous reply, for context:\n"
                       + wrap_untrusted("previous reply", previous[:PREVIOUS_REPLY_CHARS]) + "\n\n")
        prompt += f"The user's message:\n{user.content}"
        convo = [ChatMessage("system", SYSTEM), ChatMessage("user", prompt)]
        ops = None
        for _ in range(2):
            raw = await self._complete(convo, user.id)
            ops = _parse(raw)
            if ops is not None:
                break
            convo += [ChatMessage("assistant", raw),
                      ChatMessage("user", "That wasn't valid JSON. Answer with the JSON object only.")]
        if not ops:
            return []
        changes = await anyio.to_thread.run_sync(self._apply, ops[:MAX_OPS], ids, persona_scope, user.id,
                                                 conversation_id)
        if changes:
            self.messages.update(user.id, meta={**user.meta, "facts_changed": [c.model_dump() for c in changes]})
            await self.hub.publish(FactsChanged(conversation_id=conversation_id, message_id=user.id, changes=changes))
        return changes

    def _apply(self, ops: list, ids: dict, persona_scope: str, source: str, conversation_id: str) -> list[FactChange]:
        """Blocking (embeds). Each operation stands alone: a malformed or failing one is skipped, and whatever
        was applied is still returned, so it's always shown to the user (memory is never silent)."""
        changes: list[FactChange] = []
        for op in ops:
            try:
                change = self._apply_one(op, ids, persona_scope, source, conversation_id)
            except Exception:
                log.warning("skipped a fact operation that failed: %r", op, exc_info=True)
                continue
            if change is not None:
                changes.append(change)
        return changes

    def _apply_one(self, op, ids: dict, persona_scope: str, source: str, conversation_id: str) -> FactChange | None:
        if not isinstance(op, dict):
            return None
        kind = op.get("op")
        text = op.get("text").strip() if isinstance(op.get("text"), str) else ""
        key = op.get("id") if isinstance(op.get("id"), str) else None  # the model may send lists, numbers…
        if kind == "add" and text:
            scope = {"user": "user", "persona": persona_scope}.get(op["scope"] if isinstance(op.get("scope"), str) else "")
            if scope is None or not valid_scope(scope):
                return None
            f = self.facts.add(scope, text, actor="extractor", source_message_id=source, conversation_id=conversation_id)
            return FactChange(fact_id=f.id, op="add", scope=scope, text=f.text)
        if kind == "update" and text and key in ids:
            old = ids[key]
            f = self.facts.update(old.id, text, actor="extractor", source_message_id=source)
            if f is not None and f.text != old.text:
                return FactChange(fact_id=f.id, op="update", scope=f.scope, text=f.text, old_text=old.text)
        if kind == "delete" and key in ids:
            old = self.facts.delete(ids[key].id, actor="extractor", source_message_id=source)
            if old is not None:
                return FactChange(fact_id=old.id, op="delete", scope=old.scope, old_text=old.text)
        return None
