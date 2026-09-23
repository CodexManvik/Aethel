import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getSocket } from "../../lib/session";
import { useSession } from "../../stores/session";
import { useTasks } from "../../stores/tasks";
import { useUi } from "../../stores/ui";
import { hydrateTasks, refreshOpenConversation } from "../conversation/useConversations";

/** Pipes websocket events into the stores; keeps the open conversation and its tasks fresh. */
export function useSessionEvents() {
  const qc = useQueryClient();
  useEffect(() => {
    const socket = getSocket();
    let wasClosed = false;
    const offEvents = socket.subscribe((ev) => {
      const ownsMessage = ev.type === "message_end" && useSession.getState().messages.some((m) => m.id === ev.message_id);
      useSession.getState().apply(ev);
      useTasks.getState().apply(ev);
      if (ev.type === "task_created" && ev.conversation_id === useSession.getState().conversationId) {
        useUi.getState().openTaskPanel(ev.task_id);
      }
      if (ev.type === "conversation_updated" || ev.type === "message_end") {
        void qc.invalidateQueries({ queryKey: ["conversations"] });
      }
      if (ownsMessage) void refreshOpenConversation().catch(() => {});
    });
    const offStatus = socket.onStatus((s) => {
      useSession.getState().setSocketStatus(s);
      if (s === "closed") wasClosed = true;
      if (s === "open" && wasClosed) {
        wasClosed = false;
        // Catch up on anything missed while disconnected: replies, and task
        // progress (hydrate swaps in the server's tasks, so approvals answered
        // or dropped meanwhile disappear from the panel).
        void refreshOpenConversation().catch(() => {});
        const conversationId = useSession.getState().conversationId;
        if (conversationId) void hydrateTasks(conversationId).catch(() => {});
      }
    });
    return () => {
      offEvents();
      offStatus();
    };
  }, [qc]);
}
