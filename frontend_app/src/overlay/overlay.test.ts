import type { ServerEvent } from "../lib/events";
import { initialOverlay, overlayReducer, overlayVisible } from "./state";

const created = (task_id: string): ServerEvent => ({
  type: "task_created", task_id, conversation_id: "c", goal: "g", user_message_id: "m", client_id: null,
});
const state = (task_id: string, s: "running" | "waiting_approval" | "done" | "cancelled"): ServerEvent => ({
  type: "task_state", task_id, conversation_id: "c", state: s, summary: null, error: null, message_id: null, message_text: null,
});
const cursor = (task_id: string, x: number, y: number, label: string): ServerEvent => ({ type: "cursor_intent", task_id, x, y, label });

test("shows at the cursor intent (physical pixels scaled to CSS) and hides when the task ends", () => {
  let s = overlayReducer(initialOverlay, created("t1"), 1.25);
  expect(overlayVisible(s)).toBe(false); // nothing to point at yet
  s = overlayReducer(s, cursor("t1", 1000, 500, "Click “Search” in firefox"), 1.25);
  expect(s).toMatchObject({ x: 800, y: 400, label: "Click “Search” in firefox" });
  expect(overlayVisible(s)).toBe(true);
  s = overlayReducer(s, state("t1", "waiting_approval"), 1.25);
  expect(overlayVisible(s)).toBe(true);
  s = overlayReducer(s, state("t1", "done"), 1.25);
  expect(overlayVisible(s)).toBe(false);
  expect(s).toEqual(initialOverlay);
});

test("a second running task keeps the cursor up when the first ends", () => {
  let s = overlayReducer(initialOverlay, cursor("a", 10, 10, "x"), 1);
  s = overlayReducer(s, state("b", "running"), 1);
  s = overlayReducer(s, state("a", "cancelled"), 1);
  expect(overlayVisible(s)).toBe(true);
});
