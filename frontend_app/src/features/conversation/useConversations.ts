import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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

export function useSendMessage() {
  const qc = useQueryClient();
  return useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      let conversationId = useSession.getState().conversationId;
      if (!conversationId) {
        const conv = await api<Conversation>("/api/conversations", { method: "POST", body: JSON.stringify({}) });
        useSession.getState().setConversation(conv.id, []);
        conversationId = conv.id;
        void qc.invalidateQueries({ queryKey: ["conversations"] });
      }
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
      let conversationId = useSession.getState().conversationId;
      if (!conversationId) {
        const conv = await api<Conversation>("/api/conversations", { method: "POST", body: JSON.stringify({}) });
        useSession.getState().setConversation(conv.id, []);
        conversationId = conv.id;
        void qc.invalidateQueries({ queryKey: ["conversations"] });
      }
      const clientId = newClientId();
      useSession.getState().addPendingUser(trimmed, clientId);
      getSocket().send({ type: "start_task", conversation_id: conversationId, goal: trimmed, client_id: clientId });
    },
    [qc],
  );
}
