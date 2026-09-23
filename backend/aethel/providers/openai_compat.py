"""One client for every OpenAI-compatible endpoint (Groq, Gemini, OpenRouter,
custom servers and local llama.cpp)."""
from typing import AsyncIterator

import httpx
import openai
from openai import AsyncOpenAI

from .base import ChatMessage, ProviderError, StreamDone, StreamEvent, TextDelta, ToolCall, ToolCallsReady, ToolSpec

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _error_text(exc: openai.APIStatusError) -> str:
    body = exc.body
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
    return str(exc)[:300]


def _wire_message(m: ChatMessage) -> dict:
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
    if m.tool_calls:
        return {
            "role": m.role,
            "content": m.content or None,
            "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in m.tool_calls
            ],
        }
    return {"role": m.role, "content": m.content}


def _wire_tools(tools: list[ToolSpec]) -> list[dict]:
    return [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
        for t in tools
    ]


class OpenAICompatProvider:
    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str,
        model: str,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ):
        self.label = f"{provider}:{model}"
        self.model = model
        self._client = AsyncOpenAI(
            base_url=base_url, api_key=api_key, http_client=http_client, timeout=timeout, max_retries=0
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
        tools: list[ToolSpec] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        kwargs: dict = {
            "model": self.model,
            "messages": [_wire_message(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = _wire_tools(tools)
        try:
            stream = await self._client.chat.completions.create(**kwargs)
            finish = None
            pending: dict[int, dict] = {}  # tool calls arrive as deltas keyed by index
            last_index: int | None = None
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta is not None:
                    if delta.content:
                        yield TextDelta(delta.content)
                    for tc in delta.tool_calls or []:
                        if tc.index is not None:
                            index = tc.index
                        else:
                            starts_new = tc.id is not None or (
                                tc.function is not None
                                and tc.function.name
                                and last_index is not None
                                and pending.get(last_index, {}).get("name")
                            )
                            if starts_new:
                                index = (max(pending) + 1) if pending else 0
                            else:
                                index = last_index if last_index is not None else 0
                        last_index = index
                        slot = pending.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function is not None:
                            if tc.function.name:
                                slot["name"] += tc.function.name
                            if tc.function.arguments:
                                slot["arguments"] += tc.function.arguments
                if choice.finish_reason:
                    finish = choice.finish_reason
            if pending:
                yield ToolCallsReady([
                    ToolCall(id=s["id"] or f"call_{i}", name=s["name"], arguments=s["arguments"] or "{}")
                    for i, s in sorted(pending.items())
                ])
            yield StreamDone(finish)
        except openai.APIStatusError as exc:
            raise ProviderError(
                f"{self.label}: HTTP {exc.status_code}: {_error_text(exc)}",
                retryable=exc.status_code in RETRYABLE_STATUS,
                status=exc.status_code,
            ) from exc
        except (openai.APIConnectionError, httpx.TransportError) as exc:  # timeouts, drops mid-stream
            raise ProviderError(f"{self.label}: {exc.__class__.__name__}", retryable=True) from exc

    async def list_models(self) -> list[str]:
        try:
            page = await self._client.models.list()
        except openai.APIStatusError as exc:
            raise ProviderError(
                f"{self.label}: HTTP {exc.status_code}: {_error_text(exc)}", retryable=False, status=exc.status_code
            ) from exc
        except openai.APIConnectionError as exc:
            raise ProviderError(f"{self.label}: {exc.__class__.__name__}", retryable=True) from exc
        return sorted(m.id for m in page.data)
