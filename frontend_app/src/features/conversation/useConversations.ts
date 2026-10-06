import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { newClientId } from "../../lib/ids";
import { getSocket } from "../../lib/session";
import type { Conversation, Message, TaskDetail } from "../../lib/types";
import { toUiMessages, useSession } from "../../stores/session";
import { TERMINAL_STATES, useTasks } from "../../stores/tasks";
import { useUi } from "../../stores/ui";

export function useConversations() {
  return useQuery({ queryKey: ["conversations"], queryFn: () => api<Conversation[]>("/api/conversations") });
}

export async function hydrateTasks(conversationId: string): Promise<void> {
  const details = await api<TaskDetail[]>(`/api/tasks?conversation_id=${encodeURIComponent(conversationId)}`);
  useTasks.getState().hydrate(conversationId, details);
  const live = [...details].reverse().find((d) => !TERMINAL_STATES.includes(d.task.state));
  if (live) useUi.getState().openTaskPanel(live.task.id);
}

export async function openConversation(id: string): Promise<void> {
  const messages = await api<Message[]>(`/api/conversations/${id}/messages`);
  useSession.getState().setConversation(id, toUiMessages(messages));
  useUi.getState().closeTaskPanel();
  await hydrateTasks(id).catch(() => {});
}

/** Open a conversation from elsewhere (a remembered fact's source, a recalled moment) and, when a
 * message is given, scroll to it and highlight it. */
export async function showInConversation(conversationId: string, messageId?: string | null): Promise<void> {
  await openConversation(conversationId);
  useUi.getState().setScreen("conversation");
  useUi.getState().setFocusMessage(messageId ?? null);
}

/** Re-read the open conversation (after a reply ends or the socket reconnects)
 * so the view always converges on what the server persisted. */
export async function refreshOpenConversation(): Promise<void> {
  const id = useSession.getState().conversationId;
  if (!id) return;
  const messages = await api<Message[]>(`/api/conversations/${id}/messages`);
  useSession.getState().replaceMessages(id, toUiMessages(messages));
}

export function useDeleteConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/api/conversations/${id}`, { method: "DELETE" }),
    onSuccess: (_d, id) => {
      if (useSession.getState().conversationId === id) useSession.getState().setConversation(null, []);
      void qc.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}

/** The open conversation's id, creating one first when there isn't one yet. A web choice made on the pill
 * before that is applied to the new conversation now (the conversation has to exist to hold it). */
async function ensureConversation(qc: QueryClient): Promise<string> {
  const existing = useSession.getState().conversationId;
  if (existing) return existing;
  const conv = await api<Conversation>("/api/conversations", { method: "POST", body: JSON.stringify({}) });
  useSession.getState().setConversation(conv.id, []);
  const web = useUi.getState().newChatWeb;
  if (web !== null) {
    useUi.getState().setNewChatWeb(null); // it belongs to this conversation only, however the PATCH goes
    try {
      await api<Conversation>(`/api/conversations/${conv.id}`, { method: "PATCH", body: JSON.stringify({ web }) });
    } catch {
      // the message is still sent; the conversation just follows Settings, and the user is told
      useSession.getState().addNotice("Couldn't set the web switch for this conversation, so it follows Settings.");
    }
  }
  void qc.invalidateQueries({ queryKey: ["conversations"] });
  return conv.id;
}

/** Sets the open conversation's web switch (true, false, or null to follow Settings); before a conversation
 * exists the choice is kept for when one is created. */
export function useSetWeb() {
  const qc = useQueryClient();
  return useCallback(
    async (web: boolean | null) => {
      const id = useSession.getState().conversationId;
      if (!id) return useUi.getState().setNewChatWeb(web);
      try {
        const updated = await api<Conversation>(`/api/conversations/${id}`, { method: "PATCH", body: JSON.stringify({ web }) });
        qc.setQueryData<Conversation[]>(["conversations"], (list) => list?.map((c) => (c.id === id ? updated : c)));
      } catch {
        useSession.getState().addNotice("Couldn't change the web switch for this conversation.");
      }
    },
    [qc],
  );
}

export function useSendMessage() {
  const qc = useQueryClient();
  return useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      const conversationId = await ensureConversation(qc);
      const clientId = newClientId();
      useSession.getState().addPendingUser(trimmed, clientId);
      getSocket().send({ type: "user_message", conversation_id: conversationId, text: trimmed, client_id: clientId });
    },
    [qc],
  );
}

export function useStartTask() {
  const qc = useQueryClient();
  return useCallback(
    async (goal: string) => {
      const trimmed = goal.trim();
      if (!trimmed) return;
      const conversationId = await ensureConversation(qc);
      const clientId = newClientId();
      useSession.getState().addPendingUser(trimmed, clientId);
      getSocket().send({ type: "start_task", conversation_id: conversationId, goal: trimmed, client_id: clientId });
    },
    [qc],
  );
}
