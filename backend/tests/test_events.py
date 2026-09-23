import json

import pytest
from pydantic import ValidationError

from aethel.api.events import (
    SCHEMA_PATH, ErrorEvent, MessageStart, StopGeneration, Token, UserMessage,
    client_event_adapter, export_schema, server_event_adapter,
)


def test_server_events_round_trip_with_type():
    ev = Token(message_id="m1", text="hi")
    raw = ev.model_dump_json()
    assert json.loads(raw)["type"] == "token"
    assert server_event_adapter.validate_json(raw) == ev


def test_client_events_parse_by_discriminator():
    assert isinstance(
        client_event_adapter.validate_json('{"type":"user_message","conversation_id":"c","text":"hi"}'), UserMessage
    )
    assert isinstance(client_event_adapter.validate_json('{"type":"stop_generation","message_id":"m"}'), StopGeneration)


@pytest.mark.parametrize("raw", [
    '{"type":"nope"}',
    '{"type":"user_message","conversation_id":"c","text":""}',
    '{"type":"user_message","conversation_id":"c","text":"hi","extra":1}',
])
def test_invalid_client_events_rejected(raw):
    with pytest.raises(ValidationError):
        client_event_adapter.validate_json(raw)


def test_schema_marks_type_required_on_every_event():
    defs = export_schema()["$defs"]
    for name in ("MessageStart", "Token", "MessageEnd", "ProviderSwitched", "ConversationUpdated",
                 "ErrorEvent", "UserMessage", "StopGeneration"):
        assert "type" in defs[name]["required"], name
    assert "client_id" in defs["MessageStart"]["required"]


def test_committed_schema_is_up_to_date():
    expected = json.dumps(export_schema(), indent=2, sort_keys=True) + "\n"
    assert SCHEMA_PATH.read_text(encoding="utf-8") == expected, (
        "Run: py -3.11 scripts/gen_event_schema.py  (then pnpm gen:types in frontend_app)"
    )


def test_defaults_serialise():
    ev = MessageStart(conversation_id="c", message_id="m", user_message_id="u", client_id=None)
    assert json.loads(ev.model_dump_json())["role"] == "assistant"
    assert ErrorEvent(message="x", code="internal").message_id is None
