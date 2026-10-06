/* Generated from backend/aethel/protocol.py by pnpm gen:types. Do not edit. */

export type Client = UserMessage | StopGeneration | StartTask | TaskControl | ApprovalDecision | KillSwitch;
export type ClientId = string | null;
export type ConversationId = string;
export type Text = string;
export type Type = "user_message";
export type MessageId = string;
export type Type1 = "stop_generation";
export type ClientId1 = string | null;
export type ConversationId1 = string;
export type Goal = string;
export type Type2 = "start_task";
export type Action = "pause" | "resume" | "cancel";
export type TaskId = string;
export type Type3 = "task_control";
export type ApprovalId = string;
export type Decision = "allow_once" | "allow_task" | "deny";
export type Type4 = "approval_decision";
export type Type5 = "kill_switch";
export type Server =
  | MessageStart
  | Token
  | MessageEnd
  | ProviderSwitched
  | ConversationUpdated
  | ErrorEvent
  | TaskCreated
  | TaskPlan
  | PlanProgress
  | TaskNote
  | StepStarted
  | StepFinished
  | ApprovalNeeded
  | ApprovalResolved
  | VerificationResult
  | TaskState
  | SkillLearned
  | CursorIntent
  | FactsChanged
  | ContextUsed;
export type ClientId2 = string | null;
export type ConversationId2 = string;
export type MessageId1 = string;
export type Role = "assistant";
export type Type6 = "message_start";
export type UserMessageId = string;
export type MessageId2 = string;
export type Text1 = string;
export type Type7 = "token";
export type MessageId3 = string;
export type Status = "complete" | "stopped" | "error";
export type Type8 = "message_end";
export type FromProvider = string;
export type MessageId4 = string | null;
export type Reason = string;
export type Role1 = string;
export type TaskId1 = string | null;
export type ToProvider = string;
export type Type9 = "provider_switched";
export type ConversationId3 = string;
export type Title = string;
export type Type10 = "conversation_updated";
export type Code = "no_provider" | "provider_error" | "bad_request" | "internal";
export type ConversationId4 = string | null;
export type Message = string;
export type MessageId5 = string | null;
export type Type11 = "error";
export type ClientId3 = string | null;
export type ConversationId5 = string;
export type Goal1 = string;
export type TaskId2 = string;
export type Type12 = "task_created";
export type UserMessageId1 = string;
export type Checks = string[];
export type Steps = string[];
export type TaskId3 = string;
export type Type13 = "task_plan";
export type Index = number;
export type TaskId4 = string;
export type Type14 = "plan_progress";
export type TaskId5 = string;
export type Text2 = string;
export type Type15 = "task_note";
export type StepId = string;
export type Summary = string;
export type TaskId6 = string;
export type Tool = string;
export type Type16 = "step_started";
export type Verdict = "allow" | "ask" | "deny";
export type Detail = string;
export type DurationMs = number;
export type Ok = boolean;
export type StepId1 = string;
export type TaskId7 = string;
export type Type17 = "step_finished";
export type ApprovalId1 = string;
export type Reason1 = string;
export type StepId2 = string;
export type Summary1 = string;
export type TaskId8 = string;
export type Tier = "read" | "write" | "irreversible";
export type Tool1 = string;
export type Type18 = "approval_needed";
export type ApprovalId2 = string;
export type Decision1 = "allow_once" | "allow_task" | "deny";
export type TaskId9 = string;
export type Type19 = "approval_resolved";
export type Description = string;
export type Detail1 = string;
export type Passed = boolean;
export type Results = CheckOutcome[];
export type TaskId10 = string;
export type Type20 = "verification";
export type ConversationId6 = string;
export type Error = string | null;
export type MessageId6 = string | null;
export type MessageText = string | null;
export type State =
  "planning" | "running" | "waiting_approval" | "paused" | "verifying" | "done" | "failed" | "cancelled";
export type Summary2 = string | null;
export type TaskId11 = string;
export type Type21 = "task_state";
export type Created = boolean;
export type SkillId = string;
export type TaskId12 = string;
export type Title1 = string;
export type Type22 = "skill_learned";
export type Label = string;
export type TaskId13 = string | null;
export type Type23 = "cursor_intent";
export type X = number;
export type Y = number;
export type FactId = string;
export type OldText = string | null;
export type Op = "add" | "update" | "delete";
export type Scope = string;
export type Text3 = string | null;
export type Undone = boolean;
export type Changes = FactChange[];
export type ConversationId7 = string;
export type MessageId7 = string;
export type Type24 = "facts_changed";
export type ConversationId8 = string;
export type CreatedAt = string;
export type Id = string;
export type Text4 = string;
export type Episodes = RecalledEpisode[];
export type Id1 = string;
export type Text5 = string;
export type Facts = RecalledFact[];
export type MessageId8 = string;
export type Type25 = "context_used";

export interface AethelProtocol {
  client: Client;
  server: Server;
  [k: string]: unknown;
}
export interface UserMessage {
  client_id: ClientId;
  conversation_id: ConversationId;
  text: Text;
  type: Type;
}
export interface StopGeneration {
  message_id: MessageId;
  type: Type1;
}
export interface StartTask {
  client_id: ClientId1;
  conversation_id: ConversationId1;
  goal: Goal;
  type: Type2;
}
export interface TaskControl {
  action: Action;
  task_id: TaskId;
  type: Type3;
}
export interface ApprovalDecision {
  approval_id: ApprovalId;
  decision: Decision;
  type: Type4;
}
/**
 * Ctrl+Alt+Esc: cancel every task now (spec §4.3).
 */
export interface KillSwitch {
  type: Type5;
}
export interface MessageStart {
  client_id: ClientId2;
  conversation_id: ConversationId2;
  message_id: MessageId1;
  role: Role;
  type: Type6;
  user_message_id: UserMessageId;
}
export interface Token {
  message_id: MessageId2;
  text: Text1;
  type: Type7;
}
export interface MessageEnd {
  message_id: MessageId3;
  status: Status;
  type: Type8;
}
export interface ProviderSwitched {
  from_provider: FromProvider;
  message_id: MessageId4;
  reason: Reason;
  role: Role1;
  task_id: TaskId1;
  to_provider: ToProvider;
  type: Type9;
}
export interface ConversationUpdated {
  conversation_id: ConversationId3;
  title: Title;
  type: Type10;
}
export interface ErrorEvent {
  code: Code;
  conversation_id: ConversationId4;
  message: Message;
  message_id: MessageId5;
  type: Type11;
}
export interface TaskCreated {
  client_id: ClientId3;
  conversation_id: ConversationId5;
  goal: Goal1;
  task_id: TaskId2;
  type: Type12;
  user_message_id: UserMessageId1;
}
export interface TaskPlan {
  checks: Checks;
  steps: Steps;
  task_id: TaskId3;
  type: Type13;
}
export interface PlanProgress {
  index: Index;
  task_id: TaskId4;
  type: Type14;
}
/**
 * A quiet line in a task's activity that isn't a step, e.g. "Asked for Word and Excel tools".
 */
export interface TaskNote {
  task_id: TaskId5;
  text: Text2;
  type: Type15;
}
export interface StepStarted {
  step_id: StepId;
  summary: Summary;
  task_id: TaskId6;
  tool: Tool;
  type: Type16;
  verdict: Verdict;
}
export interface StepFinished {
  detail: Detail;
  duration_ms: DurationMs;
  ok: Ok;
  step_id: StepId1;
  task_id: TaskId7;
  type: Type17;
}
export interface ApprovalNeeded {
  approval_id: ApprovalId1;
  reason: Reason1;
  step_id: StepId2;
  summary: Summary1;
  task_id: TaskId8;
  tier: Tier;
  tool: Tool1;
  type: Type18;
}
export interface ApprovalResolved {
  approval_id: ApprovalId2;
  decision: Decision1;
  task_id: TaskId9;
  type: Type19;
}
export interface VerificationResult {
  results: Results;
  task_id: TaskId10;
  type: Type20;
}
export interface CheckOutcome {
  description: Description;
  detail: Detail1;
  passed: Passed;
}
export interface TaskState {
  conversation_id: ConversationId6;
  error: Error;
  message_id: MessageId6;
  message_text: MessageText;
  state: State;
  summary: Summary2;
  task_id: TaskId11;
  type: Type21;
}
/**
 * After a task, reflection wrote or reinforced a skill (spec §6.3).
 */
export interface SkillLearned {
  created: Created;
  skill_id: SkillId;
  task_id: TaskId12;
  title: Title1;
  type: Type22;
}
/**
 * Where the next pointer action lands, for the ghost cursor overlay (spec §4.4). Physical pixels.
 */
export interface CursorIntent {
  label: Label;
  task_id: TaskId13;
  type: Type23;
  x: X;
  y: Y;
}
/**
 * Fact extraction changed what Aethel remembers, from this user message (Phase 3 spec §2.3).
 */
export interface FactsChanged {
  changes: Changes;
  conversation_id: ConversationId7;
  message_id: MessageId7;
  type: Type24;
}
export interface FactChange {
  fact_id: FactId;
  old_text: OldText;
  op: Op;
  scope: Scope;
  text: Text3;
  undone: Undone;
}
/**
 * What memory went into a reply, sent before its first token (Phase 3 spec §4.1, §5.3).
 */
export interface ContextUsed {
  episodes: Episodes;
  facts: Facts;
  message_id: MessageId8;
  type: Type25;
}
export interface RecalledEpisode {
  conversation_id: ConversationId8;
  created_at: CreatedAt;
  id: Id;
  text: Text4;
}
export interface RecalledFact {
  id: Id1;
  text: Text5;
}
