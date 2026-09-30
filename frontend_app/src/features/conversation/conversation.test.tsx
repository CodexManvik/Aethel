import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// ESM exports can't be spied on reliably; mock the module instead.
const apiMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock }));
import { InkText } from "./InkText";
import { PersonaMessage } from "./PersonaMessage";
import { PromptBox } from "./PromptBox";
import { ConversationView } from "./ConversationView";
import { greeting, formatRelative } from "./greeting";
import { setSocketForTests } from "../../lib/session";
import { useSession } from "../../stores/session";

const wrap = (ui: ReactNode) =>
  render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);

test("InkText wraps each word in an ink-word span and keeps whitespace", () => {
  const { container } = render(<InkText text={"Hello  wide\nworld"} />);
  const words = container.querySelectorAll(".ink-word");
  expect([...words].map((w) => w.textContent)).toEqual(["Hello", "wide", "world"]);
  expect(container.textContent).toBe("Hello  wide\nworld");
});

test("PersonaMessage shows markdown when complete and an alert on error", () => {
  const { rerender } = render(
    <PersonaMessage message={{ id: "a", role: "assistant", content: "**bold** text", status: "complete" }} />,
  );
  expect(screen.getByText("bold").tagName).toBe("STRONG");
  rerender(
    <PersonaMessage message={{ id: "a", role: "assistant", content: "", status: "error", error: "groq:x: no API key", errorCode: "no_provider" }} />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("no API key");
  expect(screen.getByRole("button", { name: /open settings/i })).toBeInTheDocument();
});

test("PromptBox sends on Enter, not on Shift+Enter, and shows Stop while streaming", async () => {
  const onSend = vi.fn();
  const onStop = vi.fn();
  const { rerender } = render(<PromptBox onSend={onSend} onStop={onStop} streaming={false} />);
  const box = screen.getByRole("textbox", { name: "Message" });
  await userEvent.type(box, "line one{Shift>}{Enter}{/Shift}line two");
  expect(onSend).not.toHaveBeenCalled();
  await userEvent.type(box, "{Enter}");
  expect(onSend).toHaveBeenCalledWith("line one\nline two", "chat");
  expect(box).toHaveValue("");
  rerender(<PromptBox onSend={onSend} onStop={onStop} streaming />);
  await userEvent.click(screen.getByRole("button", { name: "Stop" }));
  expect(onStop).toHaveBeenCalled();
});

test("sending from the empty state creates a conversation then sends user_message", async () => {
  const sent: unknown[] = [];
  setSocketForTests({ send: (e) => sent.push(e), subscribe: () => () => {}, onStatus: () => () => {} });
  useSession.setState({ conversationId: null, messages: [], streamingId: null, notices: [], socketStatus: "open" });
  apiMock.mockImplementation(async (path: string) => {
    if (path === "/api/conversations") return { id: "c9", title: "", persona_id: "aethel", created_at: "", updated_at: "" };
    return [];
  });
  wrap(<ConversationView />);
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/good (morning|afternoon|evening)|hello/i);
  await userEvent.type(screen.getByRole("textbox", { name: "Message" }), "hi Aethel{Enter}");
  await waitFor(() =>
    expect(sent).toEqual([{ type: "user_message", conversation_id: "c9", text: "hi Aethel", client_id: expect.any(String) }]),
  );
  const list = screen.getByRole("log");
  expect(within(list).getByText("hi Aethel")).toBeInTheDocument();
  apiMock.mockReset();
});

test("greeting and relative time helpers", () => {
  expect(greeting(new Date(2026, 0, 1, 8))).toBe("Good morning.");
  expect(greeting(new Date(2026, 0, 1, 14))).toBe("Good afternoon.");
  expect(greeting(new Date(2026, 0, 1, 21))).toBe("Good evening.");
  const now = new Date("2026-09-23T12:00:00Z");
  expect(formatRelative("2026-09-23T11:59:30Z", now)).toBe("just now");
  expect(formatRelative("2026-09-23T09:00:00Z", now)).toBe("3 hours ago");
});

test("the header shows a private badge only in private mode", async () => {
  setSocketForTests({ send: () => {}, subscribe: () => () => {}, onStatus: () => () => {} });
  useSession.setState({ conversationId: null, messages: [], streamingId: null, notices: [], socketStatus: "open" });
  let privateMode = true;
  apiMock.mockImplementation(async (path: string) => (path === "/api/settings" ? { private_mode: privateMode } : []));
  const first = wrap(<ConversationView />);
  expect(await screen.findByText("private")).toBeInTheDocument();
  first.unmount();
  privateMode = false;
  wrap(<ConversationView />);
  await waitFor(() => expect(apiMock).toHaveBeenCalledTimes(2));
  expect(screen.queryByText("private")).not.toBeInTheDocument();
  apiMock.mockReset();
});

test("the Noted line shows what was remembered, and Undo marks it undone", async () => {
  const { UserMessage } = await import("./UserMessage");
  const change = { fact_id: "f1", op: "add" as const, scope: "user", text: "Lives in Leeds", old_text: null, undone: false };
  useSession.getState().setConversation("c1", [{ id: "u1", role: "user", content: "I live in Leeds", status: "complete", noted: [change] }]);
  apiMock.mockImplementation(async (path: string, init?: RequestInit) => {
    expect(path).toBe("/api/memory/facts/undo");
    expect(JSON.parse(String(init!.body))).toEqual({ message_id: "u1", index: 0 });
    return [{ ...change, undone: true }];
  });
  const { rerender } = render(<UserMessage message={useSession.getState().messages[0]} />);
  expect(screen.getByText("Noted: Lives in Leeds")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Undo: Noted Lives in Leeds" }));
  await waitFor(() => expect(useSession.getState().messages[0].noted?.[0].undone).toBe(true));
  rerender(<UserMessage message={useSession.getState().messages[0]} />);
  expect(screen.getByText("undone")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Undo/ })).not.toBeInTheDocument();
  apiMock.mockReset();
});

test("a reply's recall footer counts and expands", async () => {
  render(
    <PersonaMessage message={{
      id: "a", role: "assistant", content: "Pip!", status: "complete",
      recalled: { facts: [{ id: "f1", text: "Has a dog called Pip" }],
        episodes: [{ id: "3", conversation_id: "c0", text: "User: my dog is Pip\nAethel: lovely", created_at: "2026-09-28T10:00:00+00:00" }] },
    }} />,
  );
  const toggle = screen.getByRole("button", { name: "Recalled 1 fact · 1 earlier moment" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  await userEvent.click(toggle);
  const list = screen.getByRole("list", { name: "Recalled" });
  expect(within(list).getByText("Has a dog called Pip")).toBeInTheDocument();
  expect(within(list).getByRole("button", { name: /my dog is Pip/ })).toBeInTheDocument();
});

test("no recall footer while streaming or when nothing was recalled", () => {
  render(<PersonaMessage message={{ id: "a", role: "assistant", content: "hi", status: "streaming", recalled: { facts: [{ id: "f", text: "x" }], episodes: [] } }} />);
  expect(screen.queryByRole("button", { name: /Recalled/ })).not.toBeInTheDocument();
});
