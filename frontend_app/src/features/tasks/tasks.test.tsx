import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TaskPanel } from "./TaskPanel";
import { setSocketForTests } from "../../lib/session";
import { useTasks, type TaskUi } from "../../stores/tasks";
import { useUi } from "../../stores/ui";
import { PromptBox } from "../conversation/PromptBox";

const task = (over: Partial<TaskUi> = {}): TaskUi => ({
  id: "t1", conversationId: "c1", goal: "Write a rain haiku", state: "waiting_approval",
  plan: ["Write the haiku", "Save a copy"], planDone: [0], checks: [{ description: "x exists", passed: null, detail: "" }],
  steps: [{ id: "s1", tool: "fs_write", summary: "C:/Users/me/Desktop/haiku.txt", verdict: "ask", ok: null, detail: "", durationMs: null }],
  approvals: [{ id: "a1", stepId: "s1", tool: "fs_write", summary: "C:/Users/me/Desktop/haiku.txt",
    reason: "Outside the folders I'm allowed to write without asking.", tier: "write" }],
  notes: [], summary: null, error: null, ...over,
});

let sent: unknown[];
beforeEach(() => {
  sent = [];
  setSocketForTests({ send: (e) => sent.push(e), subscribe: () => () => {}, onStatus: () => () => {} });
  useUi.setState({ taskPanelId: "t1" });
});

test("shows the goal, plan with a ticked step, and an approval card that answers over the socket", async () => {
  useTasks.setState({ tasks: { t1: task() } });
  render(<TaskPanel />);
  expect(screen.getByText("Write a rain haiku")).toBeInTheDocument();
  const plan = screen.getByRole("list", { name: "Plan" });
  expect(within(plan).getAllByRole("listitem")[0]).toHaveAttribute("data-done", "true");
  const card = screen.getByRole("group", { name: /approval/i });
  expect(within(card).getByText("C:/Users/me/Desktop/haiku.txt")).toBeInTheDocument();
  await userEvent.click(within(card).getByRole("button", { name: "Allow for this task" }));
  expect(sent).toEqual([{ type: "approval_decision", approval_id: "a1", decision: "allow_task" }]);
});

test("a quiet note sits in the activity where it happened, and isn't a step", () => {
  useTasks.setState({ tasks: { t1: task({
    approvals: [], state: "running",
    steps: [{ id: "s1", tool: "fs_read", summary: "C:/a.txt", verdict: "allow", ok: true, detail: "", durationMs: 4 },
      { id: "s2", tool: "word_new", summary: "a new document", verdict: "allow", ok: true, detail: "", durationMs: 9 }],
    notes: [{ text: "Asked for Word and Excel tools", at: 1 }],
  }) } });
  render(<TaskPanel />);
  const items = within(screen.getByText("Activity").parentElement as HTMLElement).getAllByRole("listitem");
  expect(items.map((li) => li.textContent)).toEqual([
    expect.stringContaining("fs_read"), "Asked for Word and Excel tools", expect.stringContaining("word_new")]);
  expect(items[1]).toHaveAttribute("data-note", "true");
});

test("a note alone still shows the activity section", () => {
  useTasks.setState({ tasks: { t1: task({ approvals: [], steps: [], notes: [{ text: "Asked for file search tools", at: 0 }] }) } });
  render(<TaskPanel />);
  expect(screen.getByText("Asked for file search tools")).toBeInTheDocument();
});

test("web approvals read in plain words", async () => {
  const { approvalVerb } = await import("./ApprovalCard");
  expect(approvalVerb("web_read")).toBe("read a web page");
  expect(approvalVerb("web_search")).toBe("search the web");
  expect(approvalVerb("browser_click")).toBe("click on a web page");
  expect(approvalVerb("browser_select_option")).toBe("use its own browser");
});

test("irreversible approvals can't be granted for the whole task", () => {
  useTasks.setState({ tasks: { t1: task({ approvals: [{ ...task().approvals[0], tier: "irreversible" }] }) } });
  render(<TaskPanel />);
  expect(screen.queryByRole("button", { name: "Allow for this task" })).not.toBeInTheDocument();
});

test("pause and cancel send task_control; a paused task offers resume", async () => {
  useTasks.setState({ tasks: { t1: task({ state: "running", approvals: [] }) } });
  const { rerender } = render(<TaskPanel />);
  await userEvent.click(screen.getByRole("button", { name: "Pause" }));
  await userEvent.click(screen.getByRole("button", { name: "Cancel task" }));
  useTasks.setState({ tasks: { t1: task({ state: "paused", approvals: [] }) } });
  rerender(<TaskPanel />);
  await userEvent.click(screen.getByRole("button", { name: "Resume" }));
  expect(sent).toEqual([
    { type: "task_control", task_id: "t1", action: "pause" },
    { type: "task_control", task_id: "t1", action: "cancel" },
    { type: "task_control", task_id: "t1", action: "resume" },
  ]);
});

test("a finished task shows its summary and checks, and can be closed", async () => {
  useTasks.setState({ tasks: { t1: task({ state: "done", approvals: [], summary: "Saved both copies.",
    checks: [{ description: "x exists", passed: true, detail: "" }] }) } });
  render(<TaskPanel />);
  expect(screen.getByText("Saved both copies.")).toBeInTheDocument();
  expect(screen.getByText("x exists").closest("li")).toHaveAttribute("data-passed", "true");
  await userEvent.click(screen.getByRole("button", { name: "Close task panel" }));
  expect(useUi.getState().taskPanelId).toBeNull();
});

test("the Task pill switches the prompt into task mode", async () => {
  const onSend = vi.fn();
  render(<PromptBox onSend={onSend} onStop={() => {}} streaming={false} />);
  await userEvent.click(screen.getByRole("button", { name: "Task" }));
  expect(screen.getByRole("button", { name: "Task" })).toHaveAttribute("aria-pressed", "true");
  await userEvent.type(screen.getByRole("textbox", { name: "Message" }), "tidy my downloads{Enter}");
  expect(onSend).toHaveBeenCalledWith("tidy my downloads", "task");
  expect(screen.getByRole("button", { name: "Task" })).toHaveAttribute("aria-pressed", "false");
});

test("desktop and Office actions get readable approval verbs", async () => {
  const { approvalVerb } = await import("./ApprovalCard");
  expect(approvalVerb("win_click")).toBe("click");
  expect(approvalVerb("win_multi_edit")).toBe("use an app on your screen");
  expect(approvalVerb("excel_write")).toBe("work in Office");
  expect(approvalVerb("something_new")).toBe("use something_new");
});

test("Undo is offered after an Office save, not after read-only steps", () => {
  const step = (tool: string) => ({ id: tool, tool, summary: "x", verdict: "allow" as const, ok: true, detail: "", durationMs: 1 });
  useTasks.setState({ tasks: { t1: task({ state: "done", approvals: [], steps: [step("word_read")] }) } });
  const { rerender } = render(<TaskPanel />);
  expect(screen.queryByRole("button", { name: /Undo file changes/ })).not.toBeInTheDocument();
  useTasks.setState({ tasks: { t1: task({ state: "done", approvals: [], steps: [step("word_save")] }) } });
  rerender(<TaskPanel />);
  expect(screen.getByRole("button", { name: /Undo file changes/ })).toBeInTheDocument();
});
