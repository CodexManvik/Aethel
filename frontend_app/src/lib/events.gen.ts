/* Generated from backend/aethel/api/events.py by pnpm gen:types. Do not edit. */

export type Client = UserMessage | StopGeneration;
export type ClientId = string | null;
export type ConversationId = string;
export type Text = string;
export type Type = "user_message";
export type MessageId = string;
export type Type1 = "stop_generation";
export type Server = MessageStart | Token | MessageEnd | ProviderSwitched | ConversationUpdated | ErrorEvent;
export type ClientId1 = string | null;
export type ConversationId1 = string;
export type MessageId1 = string;
export type Role = "assistant";
export type Type2 = "message_start";
export type UserMessageId = string;
export type MessageId2 = string;
export type Text1 = string;
export type Type3 = "token";
export type MessageId3 = string;
export type Status = "complete" | "stopped" | "error";
export type Type4 = "message_end";
export type FromProvider = string;
export type MessageId4 = string | null;
export type Reason = string;
export type Role1 = string;
export type TaskId = string | null;
export type ToProvider = string;
export type Type5 = "provider_switched";
export type ConversationId2 = string;
export type Title = string;
export type Type6 = "conversation_updated";
export type Code = "no_provider" | "provider_error" | "bad_request" | "internal";
export type ConversationId3 = string | null;
export type Message = string;
export type MessageId5 = string | null;
export type Type7 = "error";

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
export interface MessageStart {
  client_id: ClientId1;
  conversation_id: ConversationId1;
  message_id: MessageId1;
  role: Role;
  type: Type2;
  user_message_id: UserMessageId;
}
export interface Token {
  message_id: MessageId2;
  text: Text1;
  type: Type3;
}
export interface MessageEnd {
  message_id: MessageId3;
  status: Status;
  type: Type4;
}
export interface ProviderSwitched {
  from_provider: FromProvider;
  message_id: MessageId4;
  reason: Reason;
  role: Role1;
  task_id: TaskId;
  to_provider: ToProvider;
  type: Type5;
}
export interface ConversationUpdated {
  conversation_id: ConversationId2;
  title: Title;
  type: Type6;
}
export interface ErrorEvent {
  code: Code;
  conversation_id: ConversationId3;
  message: Message;
  message_id: MessageId5;
  type: Type7;
}
