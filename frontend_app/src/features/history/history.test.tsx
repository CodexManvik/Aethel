import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TasksView, formatDuration } from "./TasksView";
import type { StepRecord, TaskRecord } from "../../lib/types";

const apiMock = vi.hoisted(() => vi.fn());
const blobMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock, apiBlob: blobMock }));

const task: TaskRecord = {
  id: "t1", conversation_id: "c", goal: "play lofi on YouTube in Firefox", state: "done", plan: [], plan_done: [],
  checks: [], summary: "Done. I used the steps I learned.", error: null, created_at: "2026-09-30T10:00:00Z",
  updated_at: "2026-09-30T10:00:05Z", active_seconds: 4.2,
};
const step = (i: number, over: Partial<StepRecord> = {}): StepRecord => ({
  id: `s${i}`, task_id: "t1", idx: i, tool: "win_click", args: {}, summary: `Step ${i}`, verdict: "allow", ok: true,
  result: "ok", duration_ms: 1200, created_at: "", decider: "macro", thumbnail: null, meta: null, ...over,
});

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:thumb");
  URL.revokeObjectURL = vi.fn();
  blobMock.mockResolvedValue(new Blob(["x"]));
  apiMock.mockImplementation(async (path: string) => {
    if (path === "/api/tasks/recent") return [task];
    if (path === "/api/tasks/t1") return { task, approvals: [], check_descriptions: [], steps: [
      step(0, { tool: "win_app", summary: "Open firefox", thumbnail: "tasks/t1/s0.jpg" }),
      step(1, { summary: "Click “Lofi hip hop radio”", decider: "agent", meta: { app: "firefox", element: { role: "link", name: "Lofi hip hop radio", window: "YouTube" } } }),
    ] };
    return {};
  });
});

const renderTasks = () => render(<QueryClientProvider client={new QueryClient()}><TasksView /></QueryClientProvider>);

test("the timeline shows each step with its picture, who decided it, and scrubs with the keyboard", async () => {
  renderTasks();
  expect(await screen.findByRole("heading", { name: "play lofi on YouTube in Firefox" })).toBeInTheDocument();
  expect(await screen.findByRole("img", { name: "The screen after: Open firefox" })).toHaveAttribute("src", "blob:thumb");
  expect(blobMock).toHaveBeenCalledWith("/api/tasks/t1/steps/s0/thumbnail");
  expect(screen.getByText("replayed")).toBeInTheDocument();
  const steps = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("button");
  steps[0].focus();
  await userEvent.keyboard("{ArrowDown}");
  expect(steps[1]).toHaveAttribute("aria-current", "step");
  expect(steps[1]).toHaveFocus();
  expect(screen.getByText("thought through")).toBeInTheDocument();
  expect(screen.getByText("link “Lofi hip hop radio” in YouTube")).toBeInTheDocument();
  expect(screen.getByText("No picture for this step")).toBeInTheDocument();
});

test("export downloads the task bundle", async () => {
  renderTasks();
  await userEvent.click(await screen.findByRole("button", { name: "Export" }));
  expect(blobMock).toHaveBeenCalledWith("/api/tasks/t1/export");
});

test("durations read naturally", () => {
  expect(formatDuration(4.2)).toBe("4 s");
  expect(formatDuration(95)).toBe("1 min 35 s");
});
