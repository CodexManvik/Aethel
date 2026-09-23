import json

import httpx
import pytest

from aethel.providers.base import ChatMessage, StreamDone, TextDelta, ToolCall, ToolCallsReady, ToolSpec
from aethel.providers.openai_compat import OpenAICompatProvider

pytestmark = pytest.mark.anyio

SPEC = ToolSpec(name="fs_read", description="Read a file",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})


def _chunk(delta, finish=None):
    return "data: " + json.dumps({"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
                                  "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"


def _provider(handler):
    return OpenAICompatProvider(provider="groq", base_url="https://x.test/v1", api_key="k", model="m",
                                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_tool_call_deltas_are_assembled_across_chunks():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        body = (
            _chunk({"role": "assistant", "content": "Let me look. "})
            + _chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                      "function": {"name": "fs_read", "arguments": ""}}]})
            + _chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{\"path\": "}}]})
            + _chunk({"tool_calls": [{"index": 0, "function": {"arguments": "\"C:/a.txt\"}"}}]})
            + _chunk({}, finish="tool_calls")
            + "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    events = [e async for e in _provider(handler).stream(
        [ChatMessage("user", "read a.txt")], temperature=0.2, max_tokens=64, tools=[SPEC])]
    assert events == [
        TextDelta("Let me look. "),
        ToolCallsReady([ToolCall(id="call_1", name="fs_read", arguments='{"path": "C:/a.txt"}')]),
        StreamDone("tool_calls"),
    ]
    assert seen["body"]["tools"] == [{"type": "function", "function": {
        "name": "fs_read", "description": "Read a file", "parameters": SPEC.parameters}}]


async def test_index_less_deltas_one_chunk_per_call_stay_separate():
    def handler(request):
        body = (
            _chunk({"tool_calls": [{"id": "call_1", "type": "function",
                                    "function": {"name": "fs_read", "arguments": '{"path": "a.txt"}'}}]})
            + _chunk({"tool_calls": [{"id": "call_2", "type": "function",
                                      "function": {"name": "fs_read", "arguments": '{"path": "b.txt"}'}}]})
            + _chunk({}, finish="tool_calls")
            + "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    events = [e async for e in _provider(handler).stream(
        [ChatMessage("user", "read files")], temperature=0.2, max_tokens=64, tools=[SPEC])]
    assert events == [
        ToolCallsReady([
            ToolCall(id="call_1", name="fs_read", arguments='{"path": "a.txt"}'),
            ToolCall(id="call_2", name="fs_read", arguments='{"path": "b.txt"}'),
        ]),
        StreamDone("tool_calls"),
    ]


async def test_index_less_deltas_multi_chunk_single_call_are_merged():
    def handler(request):
        body = (
            _chunk({"tool_calls": [{"id": "call_1", "type": "function",
                                    "function": {"name": "fs_read", "arguments": ""}}]})
            + _chunk({"tool_calls": [{"function": {"arguments": "{\"path\": "}}]})
            + _chunk({"tool_calls": [{"function": {"arguments": "\"C:/a.txt\"}"}}]})
            + _chunk({}, finish="tool_calls")
            + "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    events = [e async for e in _provider(handler).stream(
        [ChatMessage("user", "read a.txt")], temperature=0.2, max_tokens=64, tools=[SPEC])]
    assert events == [
        ToolCallsReady([ToolCall(id="call_1", name="fs_read", arguments='{"path": "C:/a.txt"}')]),
        StreamDone("tool_calls"),
    ]


async def test_tool_history_is_serialised_in_openai_shape():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text=_chunk({"content": "ok"}, "stop") + "data: [DONE]\n\n",
                              headers={"content-type": "text/event-stream"})

    history = [
        ChatMessage("user", "read it"),
        ChatMessage("assistant", "", tool_calls=[ToolCall(id="call_1", name="fs_read", arguments='{"path":"a"}')]),
        ChatMessage("tool", "file text", tool_call_id="call_1"),
    ]
    [e async for e in _provider(handler).stream(history, temperature=0.2, max_tokens=8, tools=[SPEC])]
    assert seen["body"]["messages"][1] == {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "fs_read", "arguments": '{"path":"a"}'}}]}
    assert seen["body"]["messages"][2] == {"role": "tool", "tool_call_id": "call_1", "content": "file text"}


async def test_no_tools_means_no_tools_field():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text=_chunk({"content": "hi"}, "stop") + "data: [DONE]\n\n",
                              headers={"content-type": "text/event-stream"})

    [e async for e in _provider(handler).stream([ChatMessage("user", "hi")], temperature=0.2, max_tokens=8)]
    assert "tools" not in seen["body"]


async def test_router_passes_tools_through():
    from aethel.keys import KeyStore
    from aethel.paths import db_path
    from aethel.providers.router import RoleRouter
    from aethel.settings import SettingsService
    from aethel.store.db import Database
    from tests.fakes import FakeLocal, ScriptedProvider, factory_from, tool_call

    db = Database(db_path())
    settings = SettingsService(db)
    settings.update({"roles": {"agent": [{"provider": "groq", "model": "g"}]}})
    keys = KeyStore()
    keys.set_many({"groq": "k"})
    scripted = ScriptedProvider([[tool_call("fs_read", path="a")]])
    router = RoleRouter(settings=settings, keys=keys, local=FakeLocal(), factory=factory_from({"groq:g": scripted}))
    events = [e async for e in router.stream("agent", [ChatMessage("user", "x")], tools=[SPEC])]
    assert isinstance(events[0], ToolCallsReady) and events[0].calls[0].name == "fs_read"
    assert scripted.tools_seen == [["fs_read"]]
    db.close()
