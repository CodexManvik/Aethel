import copy
import json
from itertools import count

import anyio

from aethel.providers.base import ProviderError, StreamDone, TextDelta, ToolCall, ToolCallsReady
from aethel.providers.local_llama import LocalLLMUnavailable

_call_ids = count(1)


class FakeProvider:
    """Scripted LLM. `error_at=None` + `error` set => fails before any token;
    `error_at=i` => fails right before chunk i."""

    def __init__(self, label="fake:model", chunks=("Hello", " there"), error=None, error_at=None,
                 delay=0.0, models=("model-a", "model-b")):
        self.label = label
        self.chunks = list(chunks)
        self.error = error
        self.error_at = error_at
        self.delay = delay
        self.models = list(models)
        self.calls = []
        self.max_tokens_seen = []

    async def stream(self, messages, *, temperature, max_tokens, tools=None):
        self.calls.append(list(messages))
        self.max_tokens_seen.append(max_tokens)
        if self.error is not None and self.error_at is None:
            raise self.error
        for i, chunk in enumerate(self.chunks):
            if self.error is not None and self.error_at == i:
                raise self.error
            if self.delay:
                await anyio.sleep(self.delay)
            yield TextDelta(chunk)
        yield StreamDone("stop")

    async def list_models(self):
        return self.models


class FakeLocal:
    def __init__(self, up=True, fail=None):
        self.up = up
        self.fail = fail
        self.ensure_calls = 0

    def ensure_running(self):
        self.ensure_calls += 1
        if self.fail:
            raise LocalLLMUnavailable(self.fail)

    def is_up(self):
        return self.up

    def stop(self):
        pass


def factory_from(mapping):
    """mapping: {"groq:model": FakeProvider, ...}; records calls in factory.used."""
    def factory(entry, api_key, settings):
        factory.used.append((entry.provider, entry.model, api_key))
        return mapping[f"{entry.provider}:{entry.model}"]

    factory.used = []
    return factory


def retryable(msg="rate limited"):
    return ProviderError(msg, retryable=True, status=429)


def fatal(msg="bad key"):
    return ProviderError(msg, retryable=False, status=401)


def tool_call(name, call_id=None, **args):
    """One scripted tool call, e.g. tool_call("fs_write", path="C:/x.txt", content="hi")."""
    return ToolCallsReady([ToolCall(id=call_id or f"c{next(_call_ids)}", name=name, arguments=json.dumps(args))])


class ScriptedProvider:
    """Each stream() call plays the next scripted turn (a list of TextDelta /
    ToolCallsReady events); StreamDone is appended automatically unless the
    turn scripts its own (e.g. StreamDone("length"))."""

    def __init__(self, turns, label="scripted:model"):
        self.turns = [list(t) for t in turns]
        self.label = label
        self.calls = []
        self.tools_seen = []
        self.max_tokens_seen = []

    async def stream(self, messages, *, temperature, max_tokens, tools=None):
        self.calls.append([copy.copy(m) for m in messages])  # as sent: the engine may edit them later
        self.tools_seen.append([t.name for t in tools or []])
        self.max_tokens_seen.append(max_tokens)
        if not self.turns:
            raise AssertionError("ScriptedProvider ran out of scripted turns")
        turn = self.turns.pop(0)
        for event in turn:
            await anyio.sleep(0)
            yield event
        if not any(isinstance(e, StreamDone) for e in turn):
            yield StreamDone("tool_calls" if any(isinstance(e, ToolCallsReady) for e in turn) else "stop")

    async def list_models(self):
        return ["scripted"]
