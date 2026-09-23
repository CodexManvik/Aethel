from dataclasses import dataclass
from typing import AsyncIterator, Literal, Protocol, Union


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema for the arguments object


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text exactly as the model produced it


@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: list[ToolCall] | None = None  # assistant turns that called tools
    tool_call_id: str | None = None           # tool results: which call this answers


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCallsReady:
    calls: list[ToolCall]


@dataclass
class StreamDone:
    finish_reason: str | None


StreamEvent = Union[TextDelta, ToolCallsReady, StreamDone]


class ProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class LLMProvider(Protocol):
    label: str

    def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
        tools: list[ToolSpec] | None = None,
    ) -> AsyncIterator[StreamEvent]: ...
