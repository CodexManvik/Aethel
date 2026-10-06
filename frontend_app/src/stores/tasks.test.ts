import { applyTaskEvent, fromDetail, type TaskMap } from "./tasks";

const created = { type: "task_created", task_id: "t1", conversation_id: "c1", goal: "haiku",
  user_message_id: "u1", client_id: "k1" } as const;

test("a task's full lifecycle folds into one TaskUi", () => {
  let s: TaskMap = {};
  s = applyTaskEvent(s, created);
  s = applyTaskEvent(s, { type: "task_plan", task_id: "t1", steps: ["Write", "Save"], checks: ["x exists"] });
  s = applyTaskEvent(s, { type: "step_started", task_id: "t1", step_id: "s1", tool: "fs_write", summary: "C:/x", verdict: "ask" });
  s = applyTaskEvent(s, { type: "approval_needed", approval_id: "a1", task_id: "t1", step_id: "s1", tool: "fs_write",
    summary: "C:/x", reason: "outside", tier: "write" });
  expect(s.t1.approvals.map((a) => a.id)).toEqual(["a1"]);
  s = applyTaskEvent(s, { type: "approval_resolved", approval_id: "a1", task_id: "t1", decision: "allow_once" });
  s = applyTaskEvent(s, { type: "step_finished", task_id: "t1", step_id: "s1", ok: true, detail: "Wrote 4", duration_ms: 12 });
  s = applyTaskEvent(s, { type: "plan_progress", task_id: "t1", index: 0 });
  s = applyTaskEvent(s, { type: "plan_progress", task_id: "t1", index: 0 });
  s = applyTaskEvent(s, { type: "verification", task_id: "t1", results: [{ description: "x exists", passed: true, detail: "" }] });
  s = applyTaskEvent(s, { type: "task_state", task_id: "t1", conversation_id: "c1", state: "done", summary: "Saved.",
    error: null, message_id: "m9", message_text: "Saved." });
  const t = s.t1;
  expect(t.approvals).toEqual([]);
  expect(t.planDone).toEqual([0]);
  expect(t.steps).toEqual([{ id: "s1", tool: "fs_write", summary: "C:/x", verdict: "ask", ok: true, detail: "Wrote 4", durationMs: 12 }]);
  expect(t.checks).toEqual([{ description: "x exists", passed: true, detail: "" }]);
  expect([t.state, t.summary]).toEqual(["done", "Saved."]);
});

test("a task note is kept with how many steps came before it", () => {
  let s = applyTaskEvent({}, created);
  s = applyTaskEvent(s, { type: "task_note", task_id: "t1", text: "Asked for Word and Excel tools" });
  s = applyTaskEvent(s, { type: "step_started", task_id: "t1", step_id: "s1", tool: "word_new", summary: "x", verdict: "allow" });
  s = applyTaskEvent(s, { type: "task_note", task_id: "t1", text: "Asked for file search tools" });
  expect(s.t1.notes).toEqual([{ text: "Asked for Word and Excel tools", at: 0 }, { text: "Asked for file search tools", at: 1 }]);
  expect(applyTaskEvent({}, { type: "task_note", task_id: "nope", text: "x" })).toEqual({});
});

test("events for unknown tasks are ignored and terminal states clear approvals", () => {
  const s = applyTaskEvent({}, { type: "plan_progress", task_id: "nope", index: 0 });
  expect(s).toEqual({});
  let t = applyTaskEvent({}, created);
  t = applyTaskEvent(t, { type: "approval_needed", approval_id: "a1", task_id: "t1", step_id: "s1", tool: "x",
    summary: "", reason: "", tier: "irreversible" });
  t = applyTaskEvent(t, { type: "task_state", task_id: "t1", conversation_id: "c1", state: "cancelled", summary: null,
    error: null, message_id: "m", message_text: "stopped" });
  expect(t.t1.approvals).toEqual([]);
});

test("fromDetail maps the REST shape", () => {
  const ui = fromDetail({
    task: { id: "t1", conversation_id: "c1", goal: "g", state: "paused", plan: ["a"], plan_done: [0],
      checks: [{ kind: "file_exists", path: "x" }], summary: null, error: null, created_at: "", updated_at: "" },
    steps: [{ id: "s", task_id: "t1", idx: 0, tool: "fs_read", args: {}, summary: "x", verdict: "allow", ok: true,
      result: "text\nmore", duration_ms: 3, created_at: "" }],
    approvals: [],
    check_descriptions: ["x exists"],
  });
  expect(ui.steps[0]).toEqual({ id: "s", tool: "fs_read", summary: "x", verdict: "allow", ok: true, detail: "text", durationMs: 3 });
  expect(ui.checks).toEqual([{ description: "x exists", passed: null, detail: "" }]);
});
