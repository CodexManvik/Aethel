"""Picks a provider per role, walking the failover chain from Settings."""
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable

import anyio
import httpx

from ..keys import KeyStore
from ..settings import AppSettings, RouteEntry, SettingsService
from .base import ChatMessage, LLMProvider, ProviderError, StreamEvent, ToolSpec
from .catalog import base_url_for
from .openai_compat import OpenAICompatProvider

ProviderFactory = Callable[[RouteEntry, str, AppSettings], LLMProvider]
LOCAL_API_KEY = "sk-local"


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
    def __init__(self, *, settings: SettingsService, keys: KeyStore, local, factory: ProviderFactory):
        self.settings = settings
        self.keys = keys
        self.local = local
        self.factory = factory

    def chain(self, role: str) -> list[RouteEntry]:
        s = self.settings.get()
        entries = list(s.roles.get(role, []))
        if s.private_mode:
            entries = [e for e in entries if e.provider == "local"] or [RouteEntry(provider="local", model="local")]
        return entries

    async def stream(
        self,
        role: str,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        on_switch: Callable[[ProviderSwitch], Awaitable[None]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        s = self.settings.get()
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
                api_key = self.keys.get(entry.provider)
                if not api_key:
                    errors.append(f"{label}: no API key")
                    continue
            provider = self.factory(entry, api_key, s)
            if failed_label is not None and on_switch is not None:
                await on_switch(ProviderSwitch(role, failed_label, label, errors[-1]))
            started = False
            try:
                async for event in provider.stream(
                    messages, temperature=s.temperature, max_tokens=s.max_tokens, tools=tools
                ):
                    started = True
                    yield event
                return
            except ProviderError as exc:
                if started or not exc.retryable:
                    raise
                errors.append(str(exc))
                failed_label = label
        raise NoProviderAvailable("; ".join(errors) or f"No providers configured for role '{role}'.")
