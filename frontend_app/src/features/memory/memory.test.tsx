import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryView, describeSystem1 } from "./MemoryView";
import type { AppNote, Skill } from "./memoryApi";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock }));

const skill = (over: Partial<Skill> = {}): Skill => ({
  id: "skill-haiku", title: "Write a haiku in Notepad", intent: "Write a short poem into Notepad", apps: ["notepad"],
  status: "approved", runs: 3, successes: 2, avg_duration_s: 14.2, duration_history: [48.1, 12.0, 4.1],
  last_used: "2026-09-29", macro: "none", steps: ["Open Notepad", "Type the poem"], pitfalls: [], ...over,
});
const notes: AppNote[] = [{ app: "notepad", facts: ["Ctrl+S saves"], body: "- Ctrl+S saves", updated: null }];

let calls: { path: string; init?: RequestInit }[];
beforeEach(() => {
  calls = [];
  apiMock.mockImplementation(async (path: string, init?: RequestInit) => {
    calls.push({ path, init });
    if (path === "/api/memory/skills") return [skill(), skill({ id: "skill-new", title: "Play lofi", status: "quarantined", runs: 0, duration_history: [], macro: "compiled" })];
    if (path === "/api/memory/notes") return notes;
    if (path === "/api/memory/system1") return { status: "ready" };
    if (path.startsWith("/api/memory/facts")) return [];
    return init?.body ? { ...JSON.parse(String(init.body)) } : {};
  });
});

const renderMemory = () => render(<QueryClientProvider client={new QueryClient()}><MemoryView /></QueryClientProvider>);

test("skill cards show runs, success and a duration sparkline; waiting skills come first", async () => {
  renderMemory();
  await userEvent.click(await screen.findByRole("tab", { name: "Skills" }));
  const cards = await screen.findAllByRole("article");
  expect(within(cards[0]).getByText("Play lofi")).toBeInTheDocument();
  expect(within(cards[0]).getByText("replays without thinking")).toBeInTheDocument();
  const haiku = cards[1];
  expect(within(haiku).getByText("3 runs · 67% worked")).toBeInTheDocument();
  expect(within(haiku).queryByText("replays without thinking")).not.toBeInTheDocument();
  expect(within(haiku).getByRole("img", { name: "Durations, first to latest: 48 s, 12 s, 4 s" })).toBeInTheDocument();
  expect(screen.getByText(/System 1 is ready/)).toBeInTheDocument();
});

test("approve and retire send a PATCH", async () => {
  renderMemory();
  await userEvent.click(await screen.findByRole("tab", { name: "Skills" }));
  const cards = await screen.findAllByRole("article");
  await userEvent.click(within(cards[0]).getByRole("button", { name: "Approve" }));
  await userEvent.click(within(cards[1]).getByRole("button", { name: "Retire" }));
  const patches = calls.filter((c) => c.init?.method === "PATCH").map((c) => [c.path, JSON.parse(String(c.init!.body)).status]);
  expect(patches).toEqual([["/api/memory/skills/skill-new", "approved"], ["/api/memory/skills/skill-haiku", "deprecated"]]);
});

test("tabs follow the keyboard pattern and app notes can be edited", async () => {
  renderMemory();
  const factsTab = await screen.findByRole("tab", { name: "Facts" });
  factsTab.focus();
  await userEvent.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab", { name: "Skills" })).toHaveFocus();
  await userEvent.keyboard("{ArrowRight}");
  const notesTab = screen.getByRole("tab", { name: "App notes" });
  expect(notesTab).toHaveAttribute("aria-selected", "true");
  expect(notesTab).toHaveFocus();
  const box = await screen.findByLabelText(/Notes on/);
  await userEvent.clear(box);
  await userEvent.type(box, "- Tabs restore");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  const put = calls.find((c) => c.init?.method === "PUT")!;
  expect(put.path).toBe("/api/memory/notes/notepad");
  expect(JSON.parse(String(put.init!.body))).toEqual({ body: "- Tabs restore" });
});

test("System 1 status wording", () => {
  expect(describeSystem1("failed: corrupt")).toBe("System 1 isn't available: corrupt");
});
