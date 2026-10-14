import json

import pytest
from pydantic import ValidationError

from aethel.voice.protocol import VOICE_SCHEMA_PATH, Hello, Interrupt, SpeechEnd, client_adapter, export_voice_schema


def test_committed_voice_schema_is_up_to_date():
    expected = json.dumps(export_voice_schema(), indent=2, sort_keys=True) + "\n"
    assert VOICE_SCHEMA_PATH.read_text(encoding="utf-8").replace("\r\n", "\n") == expected, (
        "Run: py -3.11 scripts/gen_event_schema.py  (then pnpm gen:types in frontend_app)")


def test_client_messages_parse_by_type():
    assert client_adapter.validate_json('{"type": "hello", "conversation_id": "c1"}') == Hello(conversation_id="c1")
    assert client_adapter.validate_json('{"type": "interrupt", "last_started_sentence_id": 3}') == Interrupt(
        last_started_sentence_id=3)
    assert client_adapter.validate_json('{"type": "interrupt", "last_started_sentence_id": null}').last_started_sentence_id is None
    assert client_adapter.validate_json('{"type": "speech_end", "duration_ms": 900}') == SpeechEnd(duration_ms=900)


@pytest.mark.parametrize("raw", [
    '{"type": "nope"}', '{"type": "hello"}', '{"type": "hello", "conversation_id": "c", "extra": 1}',
    '{"type": "speech_end", "duration_ms": -1}', '{"type": "speech_end", "duration_ms": 999999}', "[]", "not json"])
def test_bad_client_messages_are_rejected(raw):
    with pytest.raises(ValidationError):
        client_adapter.validate_json(raw)
