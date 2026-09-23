import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { newClientId } from "../../lib/ids";
import { getSocket } from "../../lib/session";
import type { Conversation, Message } from "../../lib/types";
import { toUiMessages, useSession } from "../../stores/session";

export function useConversations() {
  return useQuery({ queryKey: ["conversations"], queryFn: () => api<Conversation[]>("/api/conversations") });
}

export async function openConversation(id: string): Promise<void> {
  const messages = await api<Message[]>(`/api/conversations/${id}/messages`);
  useSession.getState().setConversation(id, toUiMessages(messages));
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
