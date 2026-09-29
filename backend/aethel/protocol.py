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


TaskStateName = Literal["planning", "running", "waiting_approval", "paused", "verifying", "done", "failed", "cancelled"]
Verdict = Literal["allow", "ask", "deny"]
Tier = Literal["read", "write", "irreversible"]
Decision = Literal["allow_once", "allow_task", "deny"]


class TaskCreated(Event):
    type: Literal["task_created"] = "task_created"
    task_id: str
    conversation_id: str
    goal: str
    user_message_id: str
    client_id: str | None = None


class TaskPlan(Event):
    type: Literal["task_plan"] = "task_plan"
    task_id: str
    steps: list[str]
    checks: list[str]  # human-readable descriptions of the postconditions


class PlanProgress(Event):
    type: Literal["plan_progress"] = "plan_progress"
    task_id: str
    index: int


class StepStarted(Event):
    type: Literal["step_started"] = "step_started"
    task_id: str
    step_id: str
    tool: str
    summary: str
    verdict: Verdict


class StepFinished(Event):
    type: Literal["step_finished"] = "step_finished"
    task_id: str
    step_id: str
    ok: bool
    detail: str
    duration_ms: int


class ApprovalNeeded(Event):
    type: Literal["approval_needed"] = "approval_needed"
    approval_id: str
    task_id: str
    step_id: str
    tool: str
    summary: str
    reason: str
    tier: Tier


class ApprovalResolved(Event):
    type: Literal["approval_resolved"] = "approval_resolved"
    approval_id: str
    task_id: str
    decision: Decision


class CheckOutcome(Event):
    description: str
    passed: bool
    detail: str


class VerificationResult(Event):
    type: Literal["verification"] = "verification"
    task_id: str
    results: list[CheckOutcome]


class TaskState(Event):
    type: Literal["task_state"] = "task_state"
    task_id: str
    conversation_id: str
    state: TaskStateName
    summary: str | None = None
    error: str | None = None
    message_id: str | None = None    # set on terminal states: the reply persisted into the conversation
    message_text: str | None = None


class SkillLearned(Event):
    """After a task, reflection wrote or reinforced a skill (spec §6.3)."""
    type: Literal["skill_learned"] = "skill_learned"
    task_id: str
    skill_id: str
    title: str
    created: bool  # False: an existing skill was reinforced


class CursorIntent(Event):
    """Where the next pointer action lands, for the ghost cursor overlay (spec §4.4). Physical pixels."""
    type: Literal["cursor_intent"] = "cursor_intent"
    task_id: str | None = None
    x: int
    y: int
    label: str


ServerEvent = Annotated[
    Union[MessageStart, Token, MessageEnd, ProviderSwitched, ConversationUpdated, ErrorEvent,
          TaskCreated, TaskPlan, PlanProgress, StepStarted, StepFinished, ApprovalNeeded, ApprovalResolved,
          VerificationResult, TaskState, SkillLearned, CursorIntent],
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


class StartTask(Event):
    type: Literal["start_task"] = "start_task"
    conversation_id: str
    goal: str = Field(min_length=1, max_length=4000)
    client_id: str | None = None


class TaskControl(Event):
    type: Literal["task_control"] = "task_control"
    task_id: str
    action: Literal["pause", "resume", "cancel"]


class ApprovalDecision(Event):
    type: Literal["approval_decision"] = "approval_decision"
    approval_id: str
    decision: Decision


class KillSwitch(Event):
    """Ctrl+Alt+Esc: cancel every task now (spec §4.3)."""
    type: Literal["kill_switch"] = "kill_switch"


ClientEvent = Annotated[Union[UserMessage, StopGeneration, StartTask, TaskControl, ApprovalDecision, KillSwitch],
                        Field(discriminator="type")]

server_event_adapter: TypeAdapter = TypeAdapter(ServerEvent)
client_event_adapter: TypeAdapter = TypeAdapter(ClientEvent)


class AethelProtocol(BaseModel):
    server: ServerEvent
    client: ClientEvent


def export_schema() -> dict:
    return AethelProtocol.model_json_schema(mode="serialization")
