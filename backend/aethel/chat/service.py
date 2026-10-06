"""Runs chat turns: persist → stream from the router → persist → emit events."""
import asyncio
import logging
import re
from contextlib import aclosing
from typing import Callable

import anyio

from ..context.builder import budget_for
from ..context.recipes import chat_context
from ..hub import EventHub
from ..protocol import (ContextUsed, ConversationUpdated, ErrorEvent, MessageEnd, MessageStart, ProviderSwitched,
                        Source, Sources, Token, UserMessage)
from ..providers.base import ChatMessage, ProviderError, TextDelta
from ..providers.router import NoProviderAvailable, ProviderSwitch, RoleRouter
from ..settings import SettingsService
from ..store.repos import Conversation, ConversationRepo, MessageRepo
from ..tools.base import Tool, ToolContext
from ..tools.web import SourceList
from .persona import system_prompt, time_note
from .web_loop import WEB_NOTE, effective_web, run_web_turn

log = logging.getLogger("aethel.chat")
TITLE_MAX = 48


def make_title(text: str) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= TITLE_MAX else flat[: TITLE_MAX - 1].rstrip() + "…"


class ChatService:
    def __init__(self, *, conversations: ConversationRepo, messages: MessageRepo, router: RoleRouter,
                 settings: SettingsService, hub: EventHub,
                 task_note: Callable[[str], str | None] | None = None,
                 facts=None, episodic=None, extractor=None,
                 web_tools: Callable[[], list[Tool]] | None = None):
        self.web_tools = web_tools  # the web tools to offer when a conversation has the web on; None: never
        self.conversations = conversations
        self.messages = messages
        self.router = router
        self.settings = settings
        self.hub = hub
        self.task_note = task_note
        self.facts = facts          # FactStore: remembered facts recalled into the prompt
        self.episodic = episodic    # EpisodicIndex: earlier exchanges recalled, and each finished one indexed
        self.extractor = extractor  # FactExtractor: runs after each reply
        self._active: dict[str, asyncio.Task] = {}
        self._partials: dict[str, list[str]] = {}
        self._stop_requested: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        # conversation_id -> [lock, number of turns holding or waiting on it]
        self._locks: dict[str, list] = {}

    def start_turn(self, event: UserMessage) -> None:
        task = asyncio.create_task(self._turn(event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def partial(self, message_id: str) -> str | None:
        """Text streamed so far for a reply still in flight, else None."""
        parts = self._partials.get(message_id)
        return "".join(parts) if parts is not None else None

    async def stop(self, message_id: str) -> bool:
        """Cancel a running reply (its turn persists the partial text and
        publishes message_end). A row still marked streaming with no task
        behind it is ended here instead. Unknown or finished: no-op."""
        task = self._active.get(message_id)
        if task is not None:
            self._stop_requested.add(message_id)
            task.cancel()
            return True
        if self.messages.mark_stopped_if_streaming(message_id):
            await self.hub.publish(MessageEnd(message_id=message_id, status="stopped"))
            return True
        return False

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def shutdown(self, timeout: float = 10.0) -> None:
        try:
            await asyncio.wait_for(self.wait_idle(), timeout=timeout)
        except asyncio.TimeoutError:
            for task in list(self._tasks):
                task.cancel()
            if self._tasks:
                await asyncio.gather(*list(self._tasks), return_exceptions=True)
        if self.extractor is not None:
            await self.extractor.shutdown(timeout)

    async def _turn(self, event: UserMessage) -> None:
        """One reply at a time per conversation. The lock is taken BEFORE the user message is persisted, so the DB
        order stays user1, assistant1, user2, assistant2 and each turn's context includes the previous reply."""
        entry = self._locks.setdefault(event.conversation_id, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                await self._run_turn(event)
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                self._locks.pop(event.conversation_id, None)

    async def _run_turn(self, event: UserMessage) -> None:
        publish = self.hub.publish
        conv = self.conversations.get(event.conversation_id)
        if conv is None:
            await publish(ErrorEvent(message="Conversation not found.", code="bad_request",
                                     conversation_id=event.conversation_id))
            return
        user_msg = self.messages.add(conv.id, "user", event.text)
        assistant = self.messages.add(conv.id, "assistant", "", status="streaming")
        self._active[assistant.id] = asyncio.current_task()
        parts = self._partials[assistant.id] = []
        status = "complete"
        web_tools = self._web_tools_for(conv)
        sources = SourceList() if web_tools else None
        try:
            await publish(MessageStart(conversation_id=conv.id, message_id=assistant.id,
                                       user_message_id=user_msg.id, client_id=event.client_id))
            if not conv.title:
                title = make_title(event.text)
                self.conversations.rename(conv.id, title)
                await publish(ConversationUpdated(conversation_id=conv.id, title=title))

            async def on_switch(sw: ProviderSwitch) -> None:
                await publish(ProviderSwitched(role=sw.role, from_provider=sw.from_label, to_provider=sw.to_label,
                                               reason=sw.reason, message_id=assistant.id))

            prompt, recalled = await self._context(conv, assistant.id, event.text, web=bool(web_tools))
            if recalled["facts"] or recalled["episodes"]:
                self.messages.update(assistant.id, meta={"context": recalled})
                await publish(ContextUsed(message_id=assistant.id, **recalled))
            if web_tools:
                s = self.settings.get()
                deltas = run_web_turn(
                    router=self.router, prompt=prompt, tools=web_tools, ctx=ToolContext(task_id=None),
                    sources=sources, publish=publish, message_id=assistant.id, on_switch=on_switch,
                    budget=budget_for(s, "chat", s.max_tokens, capped=False, entries=self.router.primary("chat")))
            else:
                deltas = self._plain_turn(prompt, on_switch, assistant.id)
            async with aclosing(deltas):
                async for text in deltas:
                    parts.append(text)
                    await publish(Token(message_id=assistant.id, text=text))
        except asyncio.CancelledError:
            status = "stopped"
            if assistant.id not in self._stop_requested:
                raise  # shutdown or a caller's cancellation: persist below, then propagate
        except NoProviderAvailable as exc:
            status = "error"
            await publish(ErrorEvent(message=str(exc), code="no_provider", message_id=assistant.id))
        except ProviderError as exc:
            status = "error"
            await publish(ErrorEvent(message=str(exc), code="provider_error", message_id=assistant.id))
        except Exception:
            log.exception("chat turn failed")
            status = "error"
            await publish(ErrorEvent(message="Something went wrong while replying.", code="internal",
                                     message_id=assistant.id))
        finally:
            self.messages.update(assistant.id, content="".join(parts), status=status)
            if sources is not None and sources.all():  # before the end of the message, so [n] can become footnotes
                listed = [Source(n=s.n, title=s.title, url=s.url) for s in sources.all()]
                meta = {**self.messages.get(assistant.id).meta, "sources": [s.model_dump() for s in listed]}
                self.messages.update(assistant.id, meta=meta)
                await publish(Sources(message_id=assistant.id, task_id=None, sources=listed))
            self._active.pop(assistant.id, None)
            self._partials.pop(assistant.id, None)
            self._stop_requested.discard(assistant.id)
            await publish(MessageEnd(message_id=assistant.id, status=status))
        await self._after_turn(conv, user_msg, assistant.id, status)

    async def _after_turn(self, conv, user_msg, assistant_id: str, status: str) -> None:
        """Memory upkeep once the reply is saved: index the exchange, then look for facts to remember."""
        if status == "complete" and self.episodic is not None and self.settings.get().memory.episodic_enabled:
            final = self.messages.get(assistant_id)
            try:
                await anyio.to_thread.run_sync(self.episodic.add_exchange, user_msg, final)
            except Exception:
                log.exception("indexing the exchange failed")
        if status in ("complete", "stopped") and self.extractor is not None:
            self.extractor.schedule(conversation_id=conv.id, persona_id=conv.persona_id, user_message_id=user_msg.id)

    def _web_tools_for(self, conv: Conversation) -> list[Tool]:
        """The web tools this conversation may use: none unless the web is on for it (private mode: never)."""
        if self.web_tools is None or not effective_web(self.settings.get(), conv):
            return []
        return self.web_tools()

    async def _plain_turn(self, prompt: list[ChatMessage], on_switch, message_id: str):
        """A reply with no tools: the model's text as it streams."""
        stream = self.router.stream("chat", prompt, on_switch=on_switch, purpose="chat_reply",
                                    ref={"message_id": message_id})
        async with aclosing(stream):
            async for ev in stream:
                if isinstance(ev, TextDelta):
                    yield ev.text

    async def _context(self, conv, exclude_id: str, query: str, web: bool = False) -> tuple[list[ChatMessage], dict]:
        settings = self.settings.get()
        history = [
            m for m in self.messages.list(conv.id, limit=settings.history_window + 1)
            if m.id != exclude_id and m.status != "error" and m.content
        ]
        note = self.task_note(conv.id) if self.task_note else None
        built, recalled = await chat_context(
            system=system_prompt(), window=[ChatMessage(m.role, m.content) for m in history], query=query,
            persona_id=conv.persona_id, facts=self.facts, episodic=self.episodic, settings=settings,
            exclude_messages={m.id for m in history},
            tail="\n\n".join(filter(None, [note, time_note(), WEB_NOTE if web else None])))
        return built.messages, recalled
