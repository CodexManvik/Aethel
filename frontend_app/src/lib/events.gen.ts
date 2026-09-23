/* Generated from backend/aethel/api/events.py by pnpm gen:types. Do not edit. */

export type Client = UserMessage | StopGeneration | StartTask | TaskControl | ApprovalDecision;
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
  | StepStarted
  | StepFinished
  | ApprovalNeeded
  | ApprovalResolved
  | VerificationResult
  | TaskState;
export type ClientId2 = string | null;
export type ConversationId2 = string;
export type MessageId1 = string;
export type Role = "assistant";
export type Type5 = "message_start";
export type UserMessageId = string;
export type MessageId2 = string;
export type Text1 = string;
export type Type6 = "token";
export type MessageId3 = string;
export type Status = "complete" | "stopped" | "error";
export type Type7 = "message_end";
export type FromProvider = string;
export type MessageId4 = string | null;
export type Reason = string;
export type Role1 = string;
export type TaskId1 = string | null;
export type ToProvider = string;
export type Type8 = "provider_switched";
export type ConversationId3 = string;
export type Title = string;
export type Type9 = "conversation_updated";
export type Code = "no_provider" | "provider_error" | "bad_request" | "internal";
export type ConversationId4 = string | null;
export type Message = string;
export type MessageId5 = string | null;
export type Type10 = "error";
export type ClientId3 = string | null;
export type ConversationId5 = string;
export type Goal1 = string;
export type TaskId2 = string;
export type Type11 = "task_created";
export type UserMessageId1 = string;
export type Checks = string[];
export type Steps = string[];
export type TaskId3 = string;
export type Type12 = "task_plan";
export type Index = number;
export type TaskId4 = string;
export type Type13 = "plan_progress";
export type StepId = string;
export type Summary = string;
export type TaskId5 = string;
export type Tool = string;
export type Type14 = "step_started";
export type Verdict = "allow" | "ask" | "deny";
export type Detail = string;
export type DurationMs = number;
export type Ok = boolean;
export type StepId1 = string;
export type TaskId6 = string;
export type Type15 = "step_finished";
export type ApprovalId1 = string;
export type Reason1 = string;
export type StepId2 = string;
export type Summary1 = string;
export type TaskId7 = string;
export type Tier = "read" | "write" | "irreversible";
export type Tool1 = string;
export type Type16 = "approval_needed";
export type ApprovalId2 = string;
export type Decision1 = "allow_once" | "allow_task" | "deny";
export type TaskId8 = string;
export type Type17 = "approval_resolved";
export type Description = string;
export type Detail1 = string;
export type Passed = boolean;
export type Results = CheckOutcome[];
export type TaskId9 = string;
export type Type18 = "verification";
export type ConversationId6 = string;
export type Error = string | null;
export type MessageId6 = string | null;
export type MessageText = string | null;
export type State =
  "planning" | "running" | "waiting_approval" | "paused" | "verifying" | "done" | "failed" | "cancelled";
export type Summary2 = string | null;
export type TaskId10 = string;
export type Type19 = "task_state";

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
export interface MessageStart {
  client_id: ClientId2;
  conversation_id: ConversationId2;
  message_id: MessageId1;
  role: Role;
  type: Type5;
  user_message_id: UserMessageId;
}
export interface Token {
  message_id: MessageId2;
  text: Text1;
  type: Type6;
}
export interface MessageEnd {
  message_id: MessageId3;
  status: Status;
  type: Type7;
}
export interface ProviderSwitched {
  from_provider: FromProvider;
  message_id: MessageId4;
  reason: Reason;
  role: Role1;
  task_id: TaskId1;
  to_provider: ToProvider;
  type: Type8;
}
export interface ConversationUpdated {
  conversation_id: ConversationId3;
  title: Title;
  type: Type9;
}
export interface ErrorEvent {
  code: Code;
  conversation_id: ConversationId4;
  message: Message;
  message_id: MessageId5;
  type: Type10;
}
export interface TaskCreated {
  client_id: ClientId3;
  conversation_id: ConversationId5;
  goal: Goal1;
  task_id: TaskId2;
  type: Type11;
  user_message_id: UserMessageId1;
}
export interface TaskPlan {
  checks: Checks;
  steps: Steps;
  task_id: TaskId3;
  type: Type12;
}
export interface PlanProgress {
  index: Index;
  task_id: TaskId4;
  type: Type13;
}
export interface StepStarted {
  step_id: StepId;
  summary: Summary;
  task_id: TaskId5;
  tool: Tool;
  type: Type14;
  verdict: Verdict;
}
export interface StepFinished {
  detail: Detail;
  duration_ms: DurationMs;
  ok: Ok;
  step_id: StepId1;
  task_id: TaskId6;
  type: Type15;
}
export interface ApprovalNeeded {
  approval_id: ApprovalId1;
  reason: Reason1;
  step_id: StepId2;
  summary: Summary1;
  task_id: TaskId7;
  tier: Tier;
  tool: Tool1;
  type: Type16;
}
export interface ApprovalResolved {
  approval_id: ApprovalId2;
  decision: Decision1;
  task_id: TaskId8;
  type: Type17;
}
export interface VerificationResult {
  results: Results;
  task_id: TaskId9;
  type: Type18;
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
  task_id: TaskId10;
  type: Type19;
}
