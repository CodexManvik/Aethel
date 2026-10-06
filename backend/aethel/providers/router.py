"""Picks a provider per role, walking the failover chain from Settings."""
import time
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable

import anyio
import httpx

from ..keys import KeyStore
from ..settings import REASONING_REPLY_TOKENS, AppSettings, RouteEntry, SettingsService
from ..usage import UsageLog, estimate_breakdown
from .base import ChatMessage, LLMProvider, ProviderError, StreamDone, StreamEvent, ToolSpec, Usage
from .catalog import api_key_for, base_url_for
from .openai_compat import OpenAICompatProvider

ProviderFactory = Callable[[RouteEntry, str, AppSettings], LLMProvider]
LOCAL_API_KEY = "sk-local"
# Background work (fact extraction) runs on a small fast model, and falls back to the chat models:
# settings saved before the utility role existed, or a missing key, still work (token spec §5.2).
ROLE_FALLBACK = {"utility": "chat"}


def make_provider_factory(http_client: httpx.AsyncClient) -> ProviderFactory:
    """The production factory: every provider it builds shares `http_client`
    (one connection pool for the whole backend)."""
    def factory(entry: RouteEntry, api_key: str, settings: AppSettings) -> LLMProvider:
        return OpenAICompatProvider(
            provider=entry.provider,
            base_url=base_url_for(entry.provider, settings),
            api_key=api_key,
            model=entry.model,
            http_client=http_client,
        )

    return factory


@dataclass
class ProviderSwitch:
    role: str
    from_label: str
    to_label: str
    reason: str


class NoProviderAvailable(Exception):
    pass


class RoleRouter:
    def __init__(self, *, settings: SettingsService, keys: KeyStore, local, factory: ProviderFactory,
                 usage: UsageLog | None = None):
        self.settings = settings
        self.keys = keys
        self.local = local
        self.factory = factory
        self.usage = usage  # every call is recorded here (token-efficiency spec §3)

    def chain(self, role: str) -> list[RouteEntry]:
        s = self.settings.get()
        entries = list(s.roles.get(role, []))
        fallback = ROLE_FALLBACK.get(role)
        if fallback:  # e.g. utility: its own small model first, then whatever chat uses
            entries += [e for e in s.roles.get(fallback, []) if e not in entries]
        if s.private_mode:
            entries = [e for e in entries if e.provider == "local"] or [RouteEntry(provider="local", model="local")]
        return entries

    def primary(self, role: str) -> list[RouteEntry]:
        """The entry a call would try first: the first with a key (or local, when local is available).
        As a one-item list, for budget_for(entries=…); empty when nothing is usable."""
        s = self.settings.get()
        for entry in self.chain(role):
            if entry.provider == "local":
                if self.local is not None:
                    return [entry]
            elif api_key_for(entry.provider, self.keys, s)[0] is not None:
                return [entry]
        return []

    async def stream(
        self,
        role: str,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        on_switch: Callable[[ProviderSwitch], Awaitable[None]] | None = None,
        max_tokens: int | None = None,  # overrides settings.max_tokens (the agent role has its own limit)
        temperature: float | None = None,  # overrides settings.temperature (e.g. low for JSON extraction)
        purpose: str = "chat_reply",       # what the call is for, in the usage log (usage.PURPOSES)
        ref: dict | None = None,           # {"task_id"} or {"message_id"} it belongs to
    ) -> AsyncIterator[StreamEvent]:
        s = self.settings.get()
        breakdown = estimate_breakdown(messages, tools) if self.usage is not None else {}
        limit = max_tokens if max_tokens is not None else s.max_tokens
        temp = temperature if temperature is not None else s.temperature
        errors: list[str] = []
        failed_label: str | None = None
        for entry in self.chain(role):
            label = f"{entry.provider}:{entry.model}"
            if entry.provider == "local":
                if self.local is None:
                    errors.append(f"{label}: local models are not available")
                    continue
                try:
                    await anyio.to_thread.run_sync(self.local.ensure_running)
                except Exception as exc:  # LocalLLMUnavailable or OS errors
                    errors.append(f"{label}: {exc}")
                    continue
                api_key = LOCAL_API_KEY
            else:
                api_key, why = api_key_for(entry.provider, self.keys, s)
                if api_key is None:
                    errors.append(f"{label}: {why}")
                    continue
            provider = self.factory(entry, api_key, s)
            if failed_label is not None and on_switch is not None:
                await on_switch(ProviderSwitch(role, failed_label, label, errors[-1]))
            started = False
            status = "cancelled"  # the consumer stopped reading (stop button, shutdown), unless set below
            usage: Usage | None = None
            t0 = time.perf_counter()
            try:
                async for event in provider.stream(
                    messages, temperature=temp, tools=tools,
                    max_tokens=max(limit, REASONING_REPLY_TOKENS) if entry.reasoning else limit,  # thinking uses it first
                ):
                    started = True
                    if isinstance(event, StreamDone):
                        usage = event.usage
                    yield event
                status = "ok"
                return
            except ProviderError as exc:
                status = "error"
                if started or not exc.retryable:
                    raise
                errors.append(str(exc))
                failed_label = label
            except Exception:
                status = "error"
                raise
            finally:
                if status == "cancelled" and usage is not None:
                    status = "ok"  # the reply was complete; the reader just stopped after the final chunk
                if self.usage is not None:
                    self.usage.record(role=role, purpose=purpose, provider=entry.provider, model=entry.model,
                                      ref=ref, usage=usage, breakdown=breakdown, status=status, started=started,
                                      latency_ms=int((time.perf_counter() - t0) * 1000))
        raise NoProviderAvailable("; ".join(errors) or f"No providers configured for role '{role}'.")
