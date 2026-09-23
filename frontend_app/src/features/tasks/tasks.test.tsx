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
  summary: null, error: null, ...over,
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
