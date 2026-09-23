"""Runs chat turns: persist → stream from the router → persist → emit events."""
import asyncio
import logging
import re
from typing import Awaitable, Callable

from pydantic import BaseModel

from ..api.events import ConversationUpdated, ErrorEvent, MessageEnd, MessageStart, ProviderSwitched, Token, UserMessage
from ..providers.base import ChatMessage, ProviderError, TextDelta
from ..providers.router import NoProviderAvailable, ProviderSwitch, RoleRouter
from ..settings import SettingsService
from ..store.repos import ConversationRepo, MessageRepo
from .persona import system_prompt

log = logging.getLogger("aethel.chat")
Emit = Callable[[BaseModel], Awaitable[None]]
TITLE_MAX = 48


def make_title(text: str) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= TITLE_MAX else flat[: TITLE_MAX - 1].rstrip() + "…"


class ChatService:
    def __init__(self, *, conversations: ConversationRepo, messages: MessageRepo, router: RoleRouter,
                 settings: SettingsService):
        self.conversations = conversations
        self.messages = messages
        self.router = router
        self.settings = settings
        self._active: dict[str, asyncio.Task] = {}
        self._tasks: set[asyncio.Task] = set()

    def start_turn(self, event: UserMessage, emit: Emit) -> None:
        task = asyncio.create_task(self._turn(event, emit))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def stop(self, message_id: str) -> bool:
        task = self._active.get(message_id)
        if task is None:
            return False
        task.cancel()
        return True

    async def wait_idle(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _turn(self, event: UserMessage, emit: Emit) -> None:
        conv = self.conversations.get(event.conversation_id)
        if conv is None:
            await emit(ErrorEvent(message="Conversation not found.", code="bad_request"))
            return
        user_msg = self.messages.add(conv.id, "user", event.text)
        assistant = self.messages.add(conv.id, "assistant", "", status="streaming")
        self._active[assistant.id] = asyncio.current_task()
        await emit(MessageStart(conversation_id=conv.id, message_id=assistant.id,
                                user_message_id=user_msg.id, client_id=event.client_id))
        if not conv.title:
            title = make_title(event.text)
            self.conversations.rename(conv.id, title)
            await emit(ConversationUpdated(conversation_id=conv.id, title=title))

        parts: list[str] = []
        status = "complete"
        try:
            async def on_switch(sw: ProviderSwitch) -> None:
                await emit(ProviderSwitched(role=sw.role, from_provider=sw.from_label,
                                            to_provider=sw.to_label, reason=sw.reason))

            async for ev in self.router.stream("chat", self._context(conv.id, assistant.id), on_switch=on_switch):
                if isinstance(ev, TextDelta):
                    parts.append(ev.text)
                    await emit(Token(message_id=assistant.id, text=ev.text))
        except asyncio.CancelledError:
            status = "stopped"
        except NoProviderAvailable as exc:
            status = "error"
            await emit(ErrorEvent(message=str(exc), code="no_provider", message_id=assistant.id))
        except ProviderError as exc:
            status = "error"
            await emit(ErrorEvent(message=str(exc), code="provider_error", message_id=assistant.id))
        except Exception:
            log.exception("chat turn failed")
            status = "error"
            await emit(ErrorEvent(message="Something went wrong while replying.", code="internal",
                                  message_id=assistant.id))
        finally:
            self.messages.update(assistant.id, content="".join(parts), status=status)
            self._active.pop(assistant.id, None)
            await emit(MessageEnd(message_id=assistant.id, status=status))

    def _context(self, conversation_id: str, exclude_id: str) -> list[ChatMessage]:
        window = self.settings.get().history_window
        history = [
            m for m in self.messages.list(conversation_id, limit=window + 1)
            if m.id != exclude_id and m.status != "error" and m.content
        ]
        return [ChatMessage("system", system_prompt())] + [ChatMessage(m.role, m.content) for m in history]
