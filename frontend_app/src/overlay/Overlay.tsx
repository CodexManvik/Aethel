import { useEffect, useReducer } from "react";
import { AnimatePresence, motion } from "motion/react";
import { getSocket } from "../lib/session";
import type { ServerEvent } from "../lib/events";
import { initialOverlay, overlayReducer, overlayVisible } from "./state";

const ACCENT = "#c2553a";

export function Overlay() {
  const [state, dispatch] = useReducer(
    (s: typeof initialOverlay, ev: ServerEvent) => overlayReducer(s, ev, window.devicePixelRatio || 1),
    initialOverlay,
  );
  useEffect(() => getSocket().subscribe(dispatch), []);
  const visible = overlayVisible(state);
  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="cursor"
          aria-hidden="true"
          initial={{ opacity: 0, x: state.x!, y: state.y! }}
          animate={{ opacity: 1, x: state.x!, y: state.y! }}
          exit={{ opacity: 0 }}
          transition={{ type: "spring", stiffness: 260, damping: 26, opacity: { duration: 0.2 } }}
          style={{ position: "fixed", left: 0, top: 0, pointerEvents: "none" }}
        >
          <div style={{ width: 18, height: 18, marginLeft: -9, marginTop: -9, borderRadius: 9999, background: ACCENT,
            opacity: 0.85, boxShadow: `0 0 0 6px ${ACCENT}33, 0 2px 10px rgba(0,0,0,.25)` }} />
          <div style={{ position: "absolute", left: 16, top: 10, whiteSpace: "nowrap", padding: "4px 10px", borderRadius: 9999,
            background: "rgba(251,249,244,.94)", color: "#1d1b17", font: "500 12px Inter, system-ui, sans-serif",
            border: "1px solid #e2dccf", boxShadow: "0 2px 8px rgba(0,0,0,.12)" }}>
            {state.label}
            <span style={{ marginLeft: 8, color: "#7a746a" }}>Ctrl+Alt+Esc to stop</span>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
