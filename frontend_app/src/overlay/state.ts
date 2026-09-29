import type { ServerEvent } from "../lib/events";

/** The ghost cursor's state (spec §4.4): where the next pointer action lands, and whether a task is running. */
export interface OverlayState {
  running: string[];
  x: number | null; // CSS pixels in the overlay window
  y: number | null;
  label: string;
}

export const initialOverlay: OverlayState = { running: [], x: null, y: null, label: "" };
const TERMINAL = ["done", "failed", "cancelled"];

/** `scale` is devicePixelRatio: the backend speaks physical pixels, the overlay CSS pixels. */
export function overlayReducer(s: OverlayState, ev: ServerEvent, scale: number): OverlayState {
  if (ev.type === "task_created" && !s.running.includes(ev.task_id)) return { ...s, running: [...s.running, ev.task_id] };
  if (ev.type === "task_state") {
    if (TERMINAL.includes(ev.state)) {
      const running = s.running.filter((id) => id !== ev.task_id);
      return running.length ? { ...s, running } : initialOverlay;
    }
    return s.running.includes(ev.task_id) ? s : { ...s, running: [...s.running, ev.task_id] };
  }
  if (ev.type === "cursor_intent") {
    const running = ev.task_id && !s.running.includes(ev.task_id) ? [...s.running, ev.task_id] : s.running;
    return { running, x: ev.x / scale, y: ev.y / scale, label: ev.label };
  }
  return s;
}

export const overlayVisible = (s: OverlayState) => s.running.length > 0 && s.x !== null;
