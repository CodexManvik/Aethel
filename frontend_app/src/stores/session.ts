import { create } from "zustand";
import type { ServerEvent } from "../lib/events";
import type { FactChange, RecalledEpisode, RecalledFact, Source } from "../lib/events.gen";
import type { Message, MessageStatus } from "../lib/types";
import type { SocketStatus } from "../lib/ws";

export interface Recalled {
  facts: RecalledFact[];
  episodes: RecalledEpisode[];
}

export interface UiPhoto {
  id: string;
  personaId: string;
  status: "pending" | "ready" | "failed";
  caption: string;
  url?: string;          // the backend path of the picture, once it's ready
  score?: number | null; // identity score; null: not checked
  error?: string;
  stale?: boolean;       // cut off when the app stopped: its retry died with it
  startedAt?: number;    // when the card started developing (ms), for the running average of how long photos take
}

export const photoUrl = (personaId: string, id: string) => `/api/personas/${personaId}/files/gallery/${id}.jpg`;

export interface UiMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: MessageStatus | "pending";
  error?: string;
  errorCode?: string;
  clientId?: string;
  noted?: FactChange[];  // user messages: what Aethel remembered from them (with Undo)
  recalled?: Recalled;   // replies: the facts and earlier moments that went into them
  activity?: string;     // replies being written: what Aethel is doing on the web right now
  sources?: Source[];    // replies that used the web: the numbered sources behind their [n]
  images?: UiPhoto[];    // replies with a photo (developing, ready or failed)
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
  pendingSources: Record<string, Source[]>; // a task's sources arrive before the message they belong to
}

let noticeSeq = 0;
const notice = (text: string): Notice => ({ id: `n${++noticeSeq}`, text });

export function toUiMessages(messages: Message[]): UiMessage[] {
  return messages
    .filter((m) => m.role !== "system")
    .map((m) => {
      const ui: UiMessage = { id: m.id, role: m.role as "user" | "assistant", content: m.content, status: m.status };
      const noted = m.meta?.facts_changed as FactChange[] | undefined;
      const recalled = m.meta?.context as Recalled | undefined;
      if (noted?.length) ui.noted = noted;
      if (recalled && (recalled.facts?.length || recalled.episodes?.length)) ui.recalled = recalled;
      const sources = m.meta?.sources as Source[] | undefined;
      if (sources?.length) ui.sources = sources;
      const images = m.meta?.images as { id: string; persona_id: string; status: UiPhoto["status"]; score?: number | null;
        caption?: string; stale?: boolean }[] | undefined;
      if (images?.length) {
        ui.images = images.map((i) => ({ id: i.id, personaId: i.persona_id, status: i.status, caption: i.caption ?? "",
          score: i.score ?? null, ...(i.stale ? { stale: true } : {}),
          ...(i.status === "ready" ? { url: photoUrl(i.persona_id, i.id) } : {}) }));
      }
      return ui;
    });
}

const setOn = (data: SessionData, id: string, patch: Partial<UiMessage>): SessionData => ({
  ...data,
  messages: data.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)),
});

/** Adds the photo to its reply, or updates it there. */
const setPhoto = (data: SessionData, messageId: string, photo: UiPhoto): SessionData => ({
  ...data,
  messages: data.messages.map((m) => {
    if (m.id !== messageId) return m;
    const images = m.images ?? [];
    const at = images.findIndex((i) => i.id === photo.id);
    return { ...m, images: at < 0 ? [...images, photo] : images.map((i, n) => (n === at ? { ...i, ...photo } : i)) };
  }),
});

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
        messages: data.messages.map((m) =>
          m.id === ev.message_id ? { ...m, content: m.content + ev.text, activity: undefined } : m),
      };
    case "message_end":
      if (!has(ev.message_id)) return data;
      return {
        ...data,
        streamingId: data.streamingId === ev.message_id ? null : data.streamingId,
        messages: data.messages.map((m) => (m.id === ev.message_id ? { ...m, status: ev.status, activity: undefined } : m)),
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
      return {
        ...data,
        messages:
          ev.conversation_id && ev.conversation_id === data.conversationId
            ? data.messages.map((m) =>
                m.status === "pending" ? { ...m, status: "error" as const, error: ev.message, errorCode: ev.code } : m,
              )
            : data.messages,
        notices: [...data.notices, notice(ev.message)],
      };
    case "provider_switched":
      if (ev.message_id && !has(ev.message_id)) return data;
      return {
        ...data,
        notices: [...data.notices, notice(`${ev.from_provider} was unavailable, so ${ev.to_provider} answered instead.`)],
      };
    case "conversation_updated":
      return data;
    case "facts_changed":
      return has(ev.message_id) ? setOn(data, ev.message_id, { noted: ev.changes }) : data;
    case "context_used":
      return has(ev.message_id) ? setOn(data, ev.message_id, { recalled: { facts: ev.facts, episodes: ev.episodes } }) : data;
    case "tool_activity":
      return ev.message_id && has(ev.message_id) ? setOn(data, ev.message_id, { activity: ev.label }) : data;
    case "sources":
      if (!ev.message_id) return data;
      return has(ev.message_id)
        ? setOn(data, ev.message_id, { sources: ev.sources })
        : { ...data, pendingSources: { ...data.pendingSources, [ev.message_id]: ev.sources } };
    case "image_pending":
      if (!ev.message_id || !has(ev.message_id)) return data;
      return setPhoto(data, ev.message_id, { id: ev.image_id, personaId: ev.persona_id, status: "pending",
        caption: ev.caption, startedAt: Date.now() });
    case "image_ready":
      if (!ev.message_id || !has(ev.message_id)) return data;
      return setPhoto(data, ev.message_id, { id: ev.image_id, personaId: ev.persona_id, status: "ready",
        caption: data.messages.find((m) => m.id === ev.message_id)?.images?.find((i) => i.id === ev.image_id)?.caption ?? "",
        url: ev.url, score: ev.identity_score });
    case "image_failed":
      if (!ev.message_id || !has(ev.message_id)) return data;
      return setPhoto(data, ev.message_id, { id: ev.image_id, personaId: ev.persona_id, status: "failed",
        caption: data.messages.find((m) => m.id === ev.message_id)?.images?.find((i) => i.id === ev.image_id)?.caption ?? "",
        error: ev.message });
    case "task_created": {
      if (ev.conversation_id !== data.conversationId) return data;
      return {
        ...data,
        messages: data.messages.map((m) =>
          ev.client_id && m.clientId === ev.client_id ? { ...m, id: ev.user_message_id, status: "complete" as const } : m,
        ),
      };
    }
    case "task_state": {
      if (ev.conversation_id !== data.conversationId || !ev.message_id || !ev.message_text) return data;
      if (has(ev.message_id)) return data;
      const { [ev.message_id]: sources, ...rest } = data.pendingSources;
      return {
        ...data,
        pendingSources: rest,
        messages: [...data.messages, { id: ev.message_id, role: "assistant", content: ev.message_text, status: "complete",
          ...(sources ? { sources } : {}) }],
      };
    }
    default:
      return data;
  }
}

interface SessionState extends SessionData {
  setConversation(id: string | null, messages: UiMessage[]): void;
  addPendingUser(text: string, clientId: string): void;
  apply(ev: ServerEvent): void;
  setSocketStatus(status: SocketStatus): void;
  dismissNotice(id: string): void;
  addNotice(text: string): void;
  replaceMessages(conversationId: string, fetched: UiMessage[]): void;
  setNoted(messageId: string, noted: FactChange[]): void;
}

export const useSession = create<SessionState>()((set) => ({
  conversationId: null,
  messages: [],
  streamingId: null,
  notices: [],
  socketStatus: "connecting",
  pendingSources: {},
  setConversation: (conversationId, messages) =>
    set({
      conversationId,
      messages,
      pendingSources: {},
      streamingId: messages.find((m) => m.status === "streaming")?.id ?? null,
    }),
  addPendingUser: (text, clientId) =>
    set((s) => ({
      messages: [...s.messages, { id: `pending_${clientId}`, role: "user", content: text, status: "pending", clientId }],
    })),
  apply: (ev) => set((s) => applyEvent(s, ev)),
  setSocketStatus: (socketStatus) => set({ socketStatus }),
  dismissNotice: (id) => set((s) => ({ notices: s.notices.filter((n) => n.id !== id) })),
  addNotice: (text) => set((s) => ({ notices: [...s.notices, notice(text)] })),
  setNoted: (messageId, noted) => set((s) => setOn(s, messageId, { noted })),
  replaceMessages: (conversationId, fetched) =>
    set((s) => {
      if (s.conversationId !== conversationId) return s;
      const known = new Set(fetched.map((m) => m.id));
      const pending = s.messages.filter((m) => m.status === "pending" && !known.has(m.id));
      // A photo event can land between the server's snapshot and this: keep the newer state of each photo.
      const before = new Map(s.messages.map((m) => [m.id, m]));
      const merged = fetched.map((m) => {
        const mine = before.get(m.id)?.images;
        if (!mine?.length) return m;
        const theirs = m.images ?? [];
        const ids = new Set(theirs.map((i) => i.id));
        const images = [...theirs.map((i) => {
          const local = mine.find((x) => x.id === i.id);
          return local && i.status === "pending" && local.status !== "pending" ? local : i;
        }), ...mine.filter((x) => !ids.has(x.id))];
        return { ...m, images };
      });
      const messages = [...merged, ...pending];
      return { messages, streamingId: messages.find((m) => m.status === "streaming")?.id ?? null };
    }),
}));
