from dataclasses import dataclass
from typing import AsyncIterator, Literal, Protocol, Union


@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class TextDelta:
    text: str


@dataclass
class StreamDone:
    finish_reason: str | None


StreamEvent = Union[TextDelta, StreamDone]


class ProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class LLMProvider(Protocol):
    label: str

    def stream(
        self, messages: list[ChatMessage], *, temperature: float, max_tokens: int
    ) -> AsyncIterator[StreamEvent]: ...
