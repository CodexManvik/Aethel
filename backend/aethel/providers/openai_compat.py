"""One client for every OpenAI-compatible endpoint (Groq, Gemini, OpenRouter,
custom servers and local llama.cpp)."""
from typing import AsyncIterator

import httpx
import openai
from openai import AsyncOpenAI

from .base import ChatMessage, ProviderError, StreamDone, StreamEvent, TextDelta

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _error_text(exc: openai.APIStatusError) -> str:
    body = exc.body
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
    return str(exc)[:300]


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
        self, messages: list[ChatMessage], *, temperature: float, max_tokens: int
    ) -> AsyncIterator[StreamEvent]:
        try:
            stream = await self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            finish = None
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.delta is not None and choice.delta.content:
                    yield TextDelta(choice.delta.content)
                if choice.finish_reason:
                    finish = choice.finish_reason
            yield StreamDone(finish)
        except openai.APIStatusError as exc:
            raise ProviderError(
                f"{self.label}: HTTP {exc.status_code}: {_error_text(exc)}",
                retryable=exc.status_code in RETRYABLE_STATUS,
                status=exc.status_code,
            ) from exc
        except openai.APIConnectionError as exc:  # includes APITimeoutError
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
