import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getSocket } from "../../lib/session";
import { useSession } from "../../stores/session";
import { refreshOpenConversation } from "../conversation/useConversations";

/** Pipes websocket events into the stores; keeps the open conversation fresh. */
export function useSessionEvents() {
  const qc = useQueryClient();
  useEffect(() => {
    const socket = getSocket();
    let wasClosed = false;
    const offEvents = socket.subscribe((ev) => {
      const ownsMessage = ev.type === "message_end" && useSession.getState().messages.some((m) => m.id === ev.message_id);
      useSession.getState().apply(ev);
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
        void refreshOpenConversation().catch(() => {});
      }
    });
    return () => {
      offEvents();
      offStatus();
    };
  }, [qc]);
}
