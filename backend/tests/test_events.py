import json

import pytest
from pydantic import ValidationError

from aethel.protocol import (
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


def test_task_events_round_trip():
    from aethel.protocol import (ApprovalDecision, ApprovalNeeded, StartTask, TaskControl, TaskState,
                                 VerificationResult, CheckOutcome)

    ev = TaskState(task_id="t", conversation_id="c", state="waiting_approval")
    assert server_event_adapter.validate_json(ev.model_dump_json()) == ev
    vr = VerificationResult(task_id="t", results=[CheckOutcome(description="x exists", passed=True, detail="")])
    assert server_event_adapter.validate_json(vr.model_dump_json()) == vr
    an = ApprovalNeeded(approval_id="a", task_id="t", step_id="s", tool="fs_write", summary="write x",
                        reason="outside allowed folders", tier="write")
    assert server_event_adapter.validate_json(an.model_dump_json()) == an
    assert isinstance(client_event_adapter.validate_json(
        '{"type":"start_task","conversation_id":"c","goal":"do it","client_id":null}'), StartTask)
    assert isinstance(client_event_adapter.validate_json(
        '{"type":"task_control","task_id":"t","action":"pause"}'), TaskControl)
    assert isinstance(client_event_adapter.validate_json(
        '{"type":"approval_decision","approval_id":"a","decision":"allow_task"}'), ApprovalDecision)


def test_task_state_rejects_unknown_states():
    import pytest as _pytest
    from pydantic import ValidationError
    from aethel.protocol import TaskState

    with _pytest.raises(ValidationError):
        TaskState(task_id="t", conversation_id="c", state="sleeping")


def test_schema_marks_type_required_on_every_event():
    defs = export_schema()["$defs"]
    for name in ("MessageStart", "Token", "MessageEnd", "ProviderSwitched", "ConversationUpdated",
                 "ErrorEvent", "UserMessage", "StopGeneration",
                 "TaskCreated", "TaskPlan", "PlanProgress", "StepStarted", "StepFinished", "ApprovalNeeded",
                 "ApprovalResolved", "VerificationResult", "TaskState", "StartTask", "TaskControl", "ApprovalDecision",
                 "FactsChanged", "ContextUsed", "TaskNote"):
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
