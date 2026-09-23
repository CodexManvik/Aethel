import json

import httpx
import pytest

from aethel.providers.base import ChatMessage, ProviderError, StreamDone, TextDelta
from aethel.providers.catalog import PROVIDERS, base_url_for
from aethel.providers.openai_compat import OpenAICompatProvider
from aethel.settings import AppSettings

pytestmark = pytest.mark.anyio


def _chunk(content=None, finish=None):
    delta = {} if content is None else {"content": content}
    return "data: " + json.dumps({
        "id": "c1", "object": "chat.completion.chunk", "created": 0, "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }) + "\n\n"


def _provider(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider(
        provider="groq", base_url="https://example.test/v1", api_key="k", model="m", http_client=client
    )


async def test_streams_text_deltas_then_done():
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        body = _chunk("Hel") + _chunk("lo") + _chunk(finish="stop") + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    provider = _provider(handler)
    events = [e async for e in provider.stream(
        [ChatMessage("system", "be nice"), ChatMessage("user", "hi")], temperature=0.5, max_tokens=64
    )]
    assert events == [TextDelta("Hel"), TextDelta("lo"), StreamDone("stop")]
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["messages"] == [
        {"role": "system", "content": "be nice"}, {"role": "user", "content": "hi"}
    ]
    assert seen["body"]["stream"] is True and seen["body"]["max_tokens"] == 64
    assert provider.label == "groq:m"


@pytest.mark.parametrize("status,retryable", [(429, True), (503, True), (401, False), (404, False)])
async def test_http_errors_map_to_provider_error(status, retryable):
    provider = _provider(lambda r: httpx.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(ProviderError) as exc:
        [e async for e in provider.stream([ChatMessage("user", "hi")], temperature=0.5, max_tokens=8)]
    assert exc.value.retryable is retryable
    assert exc.value.status == status


async def test_connection_errors_are_retryable():
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ProviderError) as exc:
        [e async for e in _provider(handler).stream([ChatMessage("user", "hi")], temperature=0.5, max_tokens=8)]
    assert exc.value.retryable is True


async def test_list_models_sorted():
    def handler(request):
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"object": "list", "data": [
            {"id": "b", "object": "model", "created": 0, "owned_by": "x"},
            {"id": "a", "object": "model", "created": 0, "owned_by": "x"},
        ]})

    assert await _provider(handler).list_models() == ["a", "b"]


def test_catalog_base_urls():
    s = AppSettings(custom_base_url="http://my.box/v1")
    assert base_url_for("groq", s) == "https://api.groq.com/openai/v1"
    assert base_url_for("gemini", s).startswith("https://generativelanguage.googleapis.com/")
    assert base_url_for("custom", s) == "http://my.box/v1"
    assert base_url_for("local", s).startswith("http://127.0.0.1:")
    assert PROVIDERS["local"].needs_key is False
