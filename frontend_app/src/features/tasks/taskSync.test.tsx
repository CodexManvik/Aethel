import { act, render, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// ESM exports can't be spied on reliably; mock the module instead.
const apiMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock }));
import { hydrateTasks, openConversation, useStartTask } from "../conversation/useConversations";
import { useSessionEvents } from "../shell/useSessionEvents";
import { setSocketForTests } from "../../lib/session";
import type { SocketStatus } from "../../lib/ws";
import type { TaskDetail, TaskStateName } from "../../lib/types";
import { useSession } from "../../stores/session";
import { useTasks, type TaskUi } from "../../stores/tasks";
import { useUi } from "../../stores/ui";

const detail = (id: string, state: TaskStateName, approvals: TaskDetail["approvals"] = []): TaskDetail => ({
  task: { id, conversation_id: "c1", goal: `goal ${id}`, state, plan: ["a"], plan_done: [], checks: [],
    summary: null, error: null, created_at: "", updated_at: "" },
  steps: [],
  approvals,
  check_descriptions: [],
});

const staleTask: TaskUi = {
  id: "t1", conversationId: "c1", goal: "goal t1", state: "waiting_approval", plan: ["a"], planDone: [], checks: [],
  steps: [], approvals: [{ id: "a1", stepId: "s1", tool: "fs_write", summary: "C:/x.txt", reason: "r", tier: "write" }],
  summary: null, error: null,
};

let sent: unknown[];
let statusListeners: ((s: SocketStatus) => void)[];
beforeEach(() => {
  sent = [];
  statusListeners = [];
  apiMock.mockReset();
  setSocketForTests({
    send: (e) => sent.push(e),
    subscribe: () => () => {},
    onStatus: (l) => {
      statusListeners.push(l);
      return () => {};
    },
  });
  useSession.setState({ conversationId: "c1", messages: [], streamingId: null, notices: [], socketStatus: "open" });
  useTasks.setState({ tasks: {} });
  useUi.setState({ taskPanelId: null });
});

test("hydrateTasks loads the server's tasks, drops stale approvals and opens the panel for a live task", async () => {
  useTasks.setState({ tasks: { t1: staleTask } });
  apiMock.mockResolvedValue([detail("t0", "done"), detail("t1", "running"), detail("t2", "paused")]);
  await hydrateTasks("c1");
  expect(apiMock).toHaveBeenCalledWith("/api/tasks?conversation_id=c1");
  const tasks = useTasks.getState().tasks;
  expect(Object.keys(tasks).sort()).toEqual(["t0", "t1", "t2"]);
  expect(tasks.t1.state).toBe("running");
  expect(tasks.t1.approvals).toEqual([]);  // answered while we were away
  expect(useUi.getState().taskPanelId).toBe("t2");  // the newest task that is still going
});

test("hydrateTasks leaves the panel closed when every task has finished", async () => {
  apiMock.mockResolvedValue([detail("t0", "done")]);
  await hydrateTasks("c1");
  expect(useUi.getState().taskPanelId).toBeNull();
});

test("useStartTask sends start_task and adds the pending user bubble", async () => {
  const qc = new QueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  const { result } = renderHook(() => useStartTask(), { wrapper });
  await act(() => result.current("  tidy my downloads  "));
  expect(sent).toHaveLength(1);
  const event = sent[0] as { type: string; conversation_id: string; goal: string; client_id: string };
  expect(event).toMatchObject({ type: "start_task", conversation_id: "c1", goal: "tidy my downloads" });
  expect(useSession.getState().messages).toEqual([
    expect.objectContaining({ role: "user", content: "tidy my downloads", status: "pending", clientId: event.client_id }),
  ]);
});

test("openConversation still shows the messages when loading its tasks fails", async () => {
  apiMock.mockImplementation(async (path: string) => {
    if (path.startsWith("/api/tasks")) throw new Error("backend hiccup");
    return [{ id: "m1", conversation_id: "c2", role: "user", content: "hello", status: "complete", meta: {}, created_at: "" }];
  });
  await openConversation("c2");
  expect(useSession.getState().conversationId).toBe("c2");
  expect(useSession.getState().messages.map((m) => m.content)).toEqual(["hello"]);
});

test("a reconnect re-reads the open conversation and its tasks", async () => {
  apiMock.mockImplementation(async (path: string) => (path.startsWith("/api/tasks") ? [detail("t1", "running")] : []));
  useTasks.setState({ tasks: { t1: staleTask } });
  const qc = new QueryClient();
  function Probe() {
    useSessionEvents();
    return null;
  }
  render(<QueryClientProvider client={qc}><Probe /></QueryClientProvider>);
  act(() => statusListeners.forEach((l) => l("closed")));
  expect(apiMock).not.toHaveBeenCalled();
  act(() => statusListeners.forEach((l) => l("open")));
  await waitFor(() => expect(useTasks.getState().tasks.t1.state).toBe("running"));
  expect(apiMock).toHaveBeenCalledWith("/api/conversations/c1/messages");
  expect(apiMock).toHaveBeenCalledWith("/api/tasks?conversation_id=c1");
  expect(useTasks.getState().tasks.t1.approvals).toEqual([]);
});
