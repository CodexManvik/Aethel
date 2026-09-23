import { create } from "zustand";
import type { ServerEvent } from "../lib/events";
import type { Message, MessageStatus } from "../lib/types";
import type { SocketStatus } from "../lib/ws";

export interface UiMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: MessageStatus | "pending";
  error?: string;
  errorCode?: string;
  clientId?: string;
}

export interface Notice {
  id: string;
  text: string;
}

export interface SessionData {
  conversationId: string | null;
  messages: UiMessage[];
  streamingId: string | null;
  notices: Notice[];
  socketStatus: SocketStatus;
}

let noticeSeq = 0;
const notice = (text: string): Notice => ({ id: `n${++noticeSeq}`, text });

export function toUiMessages(messages: Message[]): UiMessage[] {
  return messages
    .filter((m) => m.role !== "system")
    .map((m) => ({ id: m.id, role: m.role as "user" | "assistant", content: m.content, status: m.status }));
}

export function applyEvent(data: SessionData, ev: ServerEvent): SessionData {
  const has = (id: string) => data.messages.some((m) => m.id === id);
  switch (ev.type) {
    case "message_start": {
      if (ev.conversation_id !== data.conversationId) return data;
      const messages = data.messages.map((m) =>
        ev.client_id && m.clientId === ev.client_id ? { ...m, id: ev.user_message_id, status: "complete" as const } : m,
      );
      messages.push({ id: ev.message_id, role: "assistant", content: "", status: "streaming" });
      return { ...data, messages, streamingId: ev.message_id };
    }
    case "token":
      if (!has(ev.message_id)) return data;
      return {
        ...data,
        messages: data.messages.map((m) => (m.id === ev.message_id ? { ...m, content: m.content + ev.text } : m)),
      };
    case "message_end":
      if (!has(ev.message_id)) return data;
      return {
        ...data,
        streamingId: data.streamingId === ev.message_id ? null : data.streamingId,
        messages: data.messages.map((m) => (m.id === ev.message_id ? { ...m, status: ev.status } : m)),
      };
    case "error":
      if (ev.message_id && has(ev.message_id)) {
        return {
          ...data,
          messages: data.messages.map((m) =>
            m.id === ev.message_id ? { ...m, status: "error", error: ev.message, errorCode: ev.code } : m,
          ),
        };
      }
      return { ...data, notices: [...data.notices, notice(ev.message)] };
    case "provider_switched":
      return {
        ...data,
        notices: [...data.notices, notice(`${ev.from_provider} was unavailable, so ${ev.to_provider} answered instead.`)],
      };
    case "conversation_updated":
      return data;
  }
}

interface SessionState extends SessionData {
  setConversation(id: string | null, messages: UiMessage[]): void;
  addPendingUser(text: string, clientId: string): void;
  apply(ev: ServerEvent): void;
  setSocketStatus(status: SocketStatus): void;
  dismissNotice(id: string): void;
}

export const useSession = create<SessionState>()((set) => ({
  conversationId: null,
  messages: [],
  streamingId: null,
  notices: [],
  socketStatus: "connecting",
  setConversation: (conversationId, messages) =>
    set({
      conversationId,
      messages,
      streamingId: messages.find((m) => m.status === "streaming")?.id ?? null,
    }),
  addPendingUser: (text, clientId) =>
    set((s) => ({
      messages: [...s.messages, { id: `pending_${clientId}`, role: "user", content: text, status: "pending", clientId }],
    })),
  apply: (ev) => set((s) => applyEvent(s, ev)),
  setSocketStatus: (socketStatus) => set({ socketStatus }),
  dismissNotice: (id) => set((s) => ({ notices: s.notices.filter((n) => n.id !== id) })),
}));
