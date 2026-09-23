import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getSocket } from "../../lib/session";
import { useSession } from "../../stores/session";

/** Pipes websocket events into the session store; refreshes the thread list. */
export function useSessionEvents() {
  const qc = useQueryClient();
  useEffect(() => {
    const socket = getSocket();
    const offEvents = socket.subscribe((ev) => {
      useSession.getState().apply(ev);
      if (ev.type === "conversation_updated" || ev.type === "message_end") {
        void qc.invalidateQueries({ queryKey: ["conversations"] });
      }
    });
    const offStatus = socket.onStatus((s) => useSession.getState().setSocketStatus(s));
    return () => {
      offEvents();
      offStatus();
    };
  }, [qc]);
}
