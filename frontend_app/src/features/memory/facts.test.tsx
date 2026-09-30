import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryView } from "./MemoryView";
import type { Fact } from "./memoryApi";
import { useUi } from "../../stores/ui";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock }));
const toastMock = vi.hoisted(() => vi.fn());
vi.mock("sonner", async (orig) => ({ ...(await orig<typeof import("sonner")>()), toast: toastMock }));

const fact = (over: Partial<Fact> = {}): Fact => ({
  id: "fact_pip", scope: "user", text: "Has a dog called Pip", source_message_id: "msg_1", conversation_id: "conv_1",
  conversation_title: "Dogs", added_by: "extractor", created_at: "2026-09-29T10:00:00+00:00",
  updated_at: "2026-09-29T10:00:00+00:00", ...over,
});

let calls: { path: string; init?: RequestInit }[];
let facts: Fact[];
beforeEach(() => {
  calls = [];
  facts = [fact(), fact({ id: "fact_manny", scope: "persona:aethel", text: "Calls Aethel 'Ae'", conversation_id: null,
    source_message_id: null, conversation_title: null, added_by: "user" })];
  toastMock.mockReset();
  apiMock.mockImplementation(async (path: string, init?: RequestInit) => {
    calls.push({ path, init });
    if (path.startsWith("/api/memory/facts?q=")) return facts.filter((f) => f.text.toLowerCase().includes("pip"));
    if (path === "/api/memory/facts") return init?.method === "POST" ? fact({ id: "fact_new" }) : facts;
    if (path.endsWith("/history")) return [
      { id: 2, fact_id: "fact_pip", op: "update", old_text: "Has a dog", new_text: "Has a dog called Pip", actor: "extractor", source_message_id: "msg_1", created_at: "2026-09-29T10:00:00+00:00" },
      { id: 1, fact_id: "fact_pip", op: "add", old_text: null, new_text: "Has a dog", actor: "extractor", source_message_id: "msg_0", created_at: "2026-09-28T10:00:00+00:00" },
    ];
    if (path === "/api/memory/system1") return { status: "ready" };
    if (path === "/api/memory/skills" || path === "/api/memory/notes") return [];
    if (path === "/api/conversations/conv_1/messages") return [];
    if (path.startsWith("/api/tasks")) return [];
    return init?.body ? JSON.parse(String(init.body)) : {};
  });
});

const renderMemory = () => render(<QueryClientProvider client={new QueryClient()}><MemoryView /></QueryClientProvider>);

test("facts are the first tab, grouped about you and with Aethel", async () => {
  renderMemory();
  expect(await screen.findByRole("tab", { name: "Facts" })).toHaveAttribute("aria-selected", "true");
  const about = await screen.findByRole("region", { name: "About you" });
  expect(within(about).getByText("Has a dog called Pip")).toBeInTheDocument();
  expect(within(about).getByRole("button", { name: /from Dogs/ })).toBeInTheDocument();
  const withAethel = screen.getByRole("region", { name: "With Aethel" });
  expect(within(withAethel).getByText(/added by you/)).toBeInTheDocument();
});

test("search sends the query", async () => {
  renderMemory();
  await screen.findByText("Has a dog called Pip");
  await userEvent.type(screen.getByRole("searchbox", { name: "Search facts" }), "pip");
  await waitFor(() => expect(calls.some((c) => c.path === "/api/memory/facts?q=pip")).toBe(true));
  await waitFor(() => expect(screen.queryByText("Calls Aethel 'Ae'")).not.toBeInTheDocument());
});

test("clicking a fact edits it and Enter saves with a PATCH", async () => {
  renderMemory();
  await userEvent.click(await screen.findByRole("button", { name: "Has a dog called Pip" }));
  const box = screen.getByRole("textbox", { name: "Edit fact" });
  await userEvent.clear(box);
  await userEvent.type(box, "Has two dogs{Enter}");
  const patch = calls.find((c) => c.init?.method === "PATCH")!;
  expect(patch.path).toBe("/api/memory/facts/fact_pip");
  expect(JSON.parse(String(patch.init!.body))).toEqual({ text: "Has two dogs" });
});

test("delete offers an Undo that adds the same fact back", async () => {
  renderMemory();
  const card = await screen.findByRole("article", { name: "Has a dog called Pip" });
  await userEvent.click(within(card).getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(toastMock).toHaveBeenCalled());
  expect(calls.find((c) => c.init?.method === "DELETE")!.path).toBe("/api/memory/facts/fact_pip");
  const [message, opts] = toastMock.mock.calls[0];
  expect(message).toBe("Fact deleted");
  opts.action.onClick();
  await waitFor(() => expect(calls.some((c) => c.init?.method === "POST")).toBe(true));
  const post = calls.find((c) => c.init?.method === "POST")!;
  expect(JSON.parse(String(post.init!.body))).toEqual({ scope: "user", text: "Has a dog called Pip" });
});

test("history lists the changes", async () => {
  renderMemory();
  const card = await screen.findByRole("article", { name: "Has a dog called Pip" });
  await userEvent.click(within(card).getByRole("button", { name: "History" }));
  const list = await within(card).findByRole("list", { name: "History" });
  expect(within(list).getByText(/changed by Aethel: “Has a dog” → “Has a dog called Pip”/)).toBeInTheDocument();
  expect(within(list).getByText(/remembered by Aethel: “Has a dog”/)).toBeInTheDocument();
});

test("add a fact", async () => {
  renderMemory();
  await userEvent.click(await screen.findByRole("button", { name: "Add a fact" }));
  await userEvent.type(screen.getByRole("textbox", { name: "New fact" }), "Is vegetarian{Enter}");
  const post = calls.find((c) => c.init?.method === "POST")!;
  expect(JSON.parse(String(post.init!.body))).toEqual({ scope: "user", text: "Is vegetarian" });
});

test("the source link opens the conversation at that message", async () => {
  renderMemory();
  await userEvent.click(await screen.findByRole("button", { name: /from Dogs/ }));
  await waitFor(() => expect(useUi.getState().screen).toBe("conversation"));
  expect(useUi.getState().focusMessageId).toBe("msg_1");
});

test("empty state", async () => {
  facts = [];
  renderMemory();
  expect(await screen.findByText(/Nothing remembered yet/)).toBeInTheDocument();
});
