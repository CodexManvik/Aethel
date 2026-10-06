import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock }));
import { PersonaMessage } from "./PersonaMessage";
import { WebPill } from "./WebPill";
import { ConversationView } from "./ConversationView";
import { citeText } from "./Citations";
import { setSocketForTests } from "../../lib/session";
import { useSession, type UiMessage } from "../../stores/session";
import { useUi } from "../../stores/ui";
import type { AppSettings, Conversation } from "../../lib/types";

const sources = [
  { n: 1, title: "Rain in Paris", url: "https://www.a.example/rain" },
  { n: 2, title: "Weather (live)", url: "https://b.example/w_(1)" },
];
const reply = (over: Partial<UiMessage> = {}): UiMessage => ({
  id: "a1", role: "assistant", content: "It rains in April [1], see also [2]. Not [9], nor [x].", status: "complete", sources, ...over,
});
const wrap = (ui: ReactNode) => render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);

// ---- footnotes ----------------------------------------------------------------------------------------------
test("[n] becomes a footnote link only for a real source; the rest stays plain text", () => {
  render(<PersonaMessage message={reply()} />);
  const body = screen.getByText(/It rains in April/);
  const cites = [...body.querySelectorAll("sup a")];
  expect(cites.map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
    ["1", "https://www.a.example/rain"], ["2", "https://b.example/w_(1)"]]);
  expect(cites.every((a) => a.getAttribute("target") === "_blank" && a.getAttribute("rel") === "noreferrer")).toBe(true);
  expect(body.textContent).toContain("Not [9], nor [x].");
});

test("a Sources list sits under the message with each title and site", () => {
  render(<PersonaMessage message={reply()} />);
  const list = screen.getByRole("list", { name: "Sources" });
  const items = within(list).getAllByRole("listitem");
  expect(items.map((li) => li.textContent)).toEqual(["1. Rain in Paris · a.example", "2. Weather (live) · b.example"]);
  expect(within(items[0]).getByRole("link", { name: "Rain in Paris" })).toHaveAttribute("href", "https://www.a.example/rain");
});

test("no footnotes or Sources while the reply is still streaming, or when there are none", () => {
  const { rerender } = render(<PersonaMessage message={reply({ status: "streaming" })} />);
  expect(screen.queryByRole("list", { name: "Sources" })).not.toBeInTheDocument();
  rerender(<PersonaMessage message={reply({ sources: undefined })} />);
  expect(screen.queryByRole("list", { name: "Sources" })).not.toBeInTheDocument();
  expect(screen.getByText(/It rains in April/).textContent).toContain("[1]");
});

test("citeText keeps parentheses and spaces in a URL working and leaves code alone", () => {
  expect(citeText("a [1] b", [{ n: 1, title: "t", url: "https://x.example/a b_(1)" }]))
    .toBe('a [1](<https://x.example/a b_(1)> "cite") b');
  expect(citeText("link [1](https://z.example) and [1]", sources)).toBe(
    'link [1](https://z.example) and [1](<https://www.a.example/rain> "cite")');   // an existing link isn't doubled
  expect(citeText("`[1]`", sources)).toBe("`[1]`");
  expect(citeText("no sources [1]", [])).toBe("no sources [1]");
});

// ---- the activity line ------------------------------------------------------------------------------------------
test("a quiet line says what Aethel is doing on the web while it streams, and goes when it stops", () => {
  const { rerender } = render(<PersonaMessage message={reply({ status: "streaming", content: "", sources: undefined, activity: 'Searching "rain"' })} />);
  expect(screen.getByText('Searching "rain"')).toBeInTheDocument();
  rerender(<PersonaMessage message={reply({ status: "streaming", content: "It", sources: undefined, activity: undefined })} />);
  expect(screen.queryByText('Searching "rain"')).not.toBeInTheDocument();
  rerender(<PersonaMessage message={reply({ status: "complete", activity: 'Searching "rain"' })} />);
  expect(screen.queryByText('Searching "rain"')).not.toBeInTheDocument();   // never on a finished message
});

// ---- the web pill -----------------------------------------------------------------------------------------------------
const settings = (over: Partial<AppSettings> = {}) => ({ internet: false, private_mode: false, ...over }) as AppSettings;
const conv = (web: boolean | null): Conversation => ({ id: "c1", title: "t", persona_id: "aethel", created_at: "", updated_at: "", web });
let patches: unknown[];

function serve(web: boolean | null, s: AppSettings = settings()) {
  patches = [];
  apiMock.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/api/settings") return s;
    if (path === "/api/conversations" && !init) return [conv(web)];
    if (path === "/api/conversations/c1" && init?.method === "PATCH") {
      const body = JSON.parse(String(init.body));
      patches.push(body);
      return { ...conv(web), ...body };
    }
    return [];
  });
}
const inConversation = () =>
  useSession.setState({ conversationId: "c1", messages: [], streamingId: null, notices: [], socketStatus: "open" });
const pill = () => screen.findByRole("button", { name: /^Web:/ });

beforeEach(() => {
  useUi.setState({ newChatWeb: null });
  useSession.setState({ conversationId: null, messages: [], streamingId: null, notices: [], socketStatus: "open" });
});

test("the pill says whether the web is on, off, or follows Settings, and a click cycles through them", async () => {
  serve(null);
  inConversation();
  wrap(<WebPill />);
  expect(await pill()).toHaveAccessibleName("Web: follows Settings (off)");
  await userEvent.click(await pill());
  await waitFor(() => expect(patches).toEqual([{ web: true }]));
  expect(await screen.findByRole("button", { name: "Web: on" })).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(screen.getByRole("button", { name: "Web: on" }));
  await waitFor(() => expect(patches).toEqual([{ web: true }, { web: false }]));
  expect(await screen.findByRole("button", { name: "Web: off" })).toHaveAttribute("aria-pressed", "false");
  await userEvent.click(screen.getByRole("button", { name: "Web: off" }));
  await waitFor(() => expect(patches).toEqual([{ web: true }, { web: false }, { web: null }]));  // back to Settings
});

test("following Settings shows what Settings says", async () => {
  serve(null, settings({ internet: true }));
  inConversation();
  wrap(<WebPill />);
  // (it says "off" until Settings have loaded)
  expect(await screen.findByRole("button", { name: "Web: follows Settings (on)" })).toHaveAttribute("aria-pressed", "true");
});

test("private mode: the pill is off and can't be changed", async () => {
  serve(true, settings({ private_mode: true, internet: true }));
  inConversation();
  wrap(<WebPill />);
  const p = await screen.findByRole("button", { name: "Web: off in private mode" });
  expect(p).toBeDisabled();
  expect(p).toHaveAttribute("title", "Private mode keeps everything on this computer");
  await userEvent.click(p);
  expect(patches).toEqual([]);
});

test("before a conversation exists the choice waits, then is applied when it's created", async () => {
  const sent: unknown[] = [];
  setSocketForTests({ send: (e) => sent.push(e), subscribe: () => () => {}, onStatus: () => () => {} });
  const calls: string[] = [];
  apiMock.mockImplementation(async (path: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${path}${init?.body ? " " + init.body : ""}`);
    if (path === "/api/settings") return settings();
    if (path === "/api/conversations" && init?.method === "POST") return { ...conv(null), id: "c9" };
    return [];
  });
  wrap(<ConversationView />);
  await userEvent.click(await screen.findByRole("button", { name: "Web: follows Settings (off)" }));
  expect(useUi.getState().newChatWeb).toBe(true);
  expect(calls.some((c) => c.startsWith("PATCH"))).toBe(false);                // nothing to patch yet
  await userEvent.type(screen.getByRole("textbox", { name: "Message" }), "latest news?{Enter}");
  await waitFor(() => expect(sent).toHaveLength(1));
  expect(calls.filter((c) => c.startsWith("POST") || c.startsWith("PATCH"))).toEqual([
    "POST /api/conversations {}", 'PATCH /api/conversations/c9 {"web":true}']);   // created, then switched on, then sent
  expect(sent[0]).toMatchObject({ type: "user_message", conversation_id: "c9", text: "latest news?" });
  expect(useUi.getState().newChatWeb).toBeNull();
});
