"""The WebSocket protocol (single source of truth). The frontend's TypeScript types are generated from
this file: py -3.11 scripts/gen_event_schema.py && (cd frontend_app && pnpm gen:types)"""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .paths import PROJECT_ROOT

SCHEMA_PATH = PROJECT_ROOT / "frontend_app" / "src" / "lib" / "events.schema.json"


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


# ---- server → client ------------------------------------------------------
class MessageStart(Event):
    type: Literal["message_start"] = "message_start"
    conversation_id: str
    message_id: str
    user_message_id: str
    client_id: str | None = None
    role: Literal["assistant"] = "assistant"


class Token(Event):
    type: Literal["token"] = "token"
    message_id: str
    text: str


class MessageEnd(Event):
    type: Literal["message_end"] = "message_end"
    message_id: str
    status: Literal["complete", "stopped", "error"]


class ProviderSwitched(Event):
    type: Literal["provider_switched"] = "provider_switched"
    role: str
    from_provider: str
    to_provider: str
    reason: str
    message_id: str | None = None  # the chat reply this affected, if any
    task_id: str | None = None     # the task this affected, if any


class ConversationUpdated(Event):
    type: Literal["conversation_updated"] = "conversation_updated"
    conversation_id: str
    title: str


class ErrorEvent(Event):
    type: Literal["error"] = "error"
    message: str
    code: Literal["no_provider", "provider_error", "bad_request", "internal"]
    message_id: str | None = None
    conversation_id: str | None = None


ServerEvent = Annotated[
    Union[MessageStart, Token, MessageEnd, ProviderSwitched, ConversationUpdated, ErrorEvent],
    Field(discriminator="type"),
]


# ---- client → server ------------------------------------------------------
class UserMessage(Event):
    type: Literal["user_message"] = "user_message"
    conversation_id: str
    text: str = Field(min_length=1, max_length=20000)
    client_id: str | None = None


class StopGeneration(Event):
    type: Literal["stop_generation"] = "stop_generation"
    message_id: str


ClientEvent = Annotated[Union[UserMessage, StopGeneration], Field(discriminator="type")]

server_event_adapter: TypeAdapter = TypeAdapter(ServerEvent)
client_event_adapter: TypeAdapter = TypeAdapter(ClientEvent)


class AethelProtocol(BaseModel):
    server: ServerEvent
    client: ClientEvent


def export_schema() -> dict:
    return AethelProtocol.model_json_schema(mode="serialization")
