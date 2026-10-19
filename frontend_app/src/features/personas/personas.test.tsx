import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const apiMock = vi.hoisted(() => vi.fn());
const blobMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock, apiBlob: blobMock }));
import { applyPersonaAccent } from "../../lib/accent";
import { useSession } from "../../stores/session";
import { useUi } from "../../stores/ui";
import { normaliseBlend, type Persona } from "./personasApi";
import { PersonasView } from "./PersonasView";

const make = (id: string, name: string, extra: Partial<Persona> = {}): Persona => ({
  id, name, built_in: id === "aethel", anchors: [], sheet: [], accent: null, voice: { blend: { af_heart: 1 }, speed: 1 },
  image_style: "", image_frequency: "on_request", tone_engine: id !== "aethel", created: null,
  sections: { Personality: "", Backstory: "", "Speaking style": "", Boundaries: "" }, style: null, lines: 0, ...extra });

let personas: Persona[];
let calls: string[];

beforeEach(() => {
  personas = [make("aethel", "Aethel", { sections: { Personality: "You are Aethel." } })];
  calls = [];
  useUi.setState({ screen: "personas", studioPersonaId: null, newChatPersona: "aethel" });
  useSession.getState().setConversation(null, []);
  blobMock.mockReset();
  apiMock.mockReset().mockImplementation(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push(`${method} ${path}${init?.body ? " " + init.body : ""}`);
    if (path === "/api/personas" && method === "GET") return personas;
    if (path === "/api/personas/draft") {
      return make("draft", "Mira", { image_style: "soft film", sections: { Personality: "You are Mira, curious.",
        Backstory: "Lisbon.", "Speaking style": "short", Boundaries: "kind" } });
    }
    if (path === "/api/personas" && method === "POST") {
      const made = make("mira", body.name, { sections: { ...make("x", "x").sections, ...body.sections }, image_style: body.image_style });
      personas = [...personas, made];
      return made;
    }
    const m = path.match(/^\/api\/personas\/([^/]+)$/);
    if (m && method === "PATCH") {
      personas = personas.map((p) => (p.id === m[1] ? { ...p, ...body, sections: { ...p.sections, ...(body.sections ?? {}) } } : p));
      return personas.find((p) => p.id === m[1]);
    }
    if (m && method === "DELETE") {
      personas = personas.filter((p) => p.id !== m[1]);
      return undefined;
    }
    if (path === "/api/voice/voices") return { "en-us": ["af_heart", "af_bella"], "en-gb": ["bf_emma"] };
    return {};
  });
});

const view = () => render(<QueryClientProvider client={new QueryClient()}><PersonasView /></QueryClientProvider>);

test("the create flow drafts from a description, saves it, and opens the editor", async () => {
  view();
  await userEvent.click(await screen.findByRole("button", { name: /New persona/ }));
  await userEvent.type(screen.getByLabelText("Describe the persona"), "a curious marine biologist");
  await userEvent.click(screen.getByRole("button", { name: "Draft it" }));
  await waitFor(() => expect(useUi.getState().studioPersonaId).toBe("mira"));
  expect(calls).toContain('POST /api/personas/draft {"description":"a curious marine biologist"}');
  const created = calls.find((c) => c.startsWith("POST /api/personas {"))!;
  expect(JSON.parse(created.slice("POST /api/personas ".length))).toMatchObject({ name: "Mira", image_style: "soft film" });
  expect(await screen.findByLabelText("Personality")).toHaveValue("You are Mira, curious.");
  expect(screen.getByRole("button", { name: "Delete persona" })).toBeInTheDocument();
});

test("edits save together once typing pauses", async () => {
  personas.push(make("mira", "Mira"));
  useUi.setState({ studioPersonaId: "mira" });
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    view();
    const personality = await screen.findByLabelText("Personality");
    fireEvent.change(personality, { target: { value: "Curious" } });
    fireEvent.change(screen.getByLabelText("Backstory"), { target: { value: "Sea" } });
    fireEvent.change(personality, { target: { value: "Curious and dry." } });
    expect(calls.filter((c) => c.startsWith("PATCH"))).toEqual([]);
    await act(async () => { vi.advanceTimersByTime(850); });
    await waitFor(() => expect(calls.filter((c) => c.startsWith("PATCH"))).toEqual([
      'PATCH /api/personas/mira {"sections":{"Personality":"Curious and dry.","Backstory":"Sea"}}']));
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

test("the voice blend is normalised on save and previewed in the persona's words", async () => {
  expect(normaliseBlend([{ voice: "af_heart", weight: 0.6 }, { voice: "bf_emma", weight: 0.2 }])).toEqual({ af_heart: 0.75, bf_emma: 0.25 });
  expect(normaliseBlend([{ voice: "af_heart", weight: 0 }])).toEqual({ af_heart: 1 });
  personas.push(make("mira", "Mira"));
  useUi.setState({ studioPersonaId: "mira" });
  const ctx = { createBufferSource: () => ({ connect: vi.fn(), start: vi.fn() }), decodeAudioData: vi.fn(async () => ({})),
    destination: {}, close: vi.fn() };
  vi.stubGlobal("AudioContext", vi.fn(() => ctx));
  blobMock.mockResolvedValue(new Blob(["RIFF"]));
  view();
  await userEvent.click(await screen.findByRole("button", { name: /Add a voice/ }));
  await waitFor(() => expect(screen.getByLabelText("Voice 2")).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText("Voice 2"), { target: { value: "bf_emma" } });
  fireEvent.change(screen.getByLabelText("Weight of voice 1"), { target: { value: "0.9" } });
  await waitFor(() => expect(calls.some((c) => c === 'PATCH /api/personas/mira {"voice":{"blend":{"af_heart":0.75,"bf_emma":0.25},"speed":1}}')).toBe(true),
    { timeout: 3000 });
  await userEvent.click(screen.getByRole("button", { name: "Preview" }));
  await waitFor(() => expect(blobMock).toHaveBeenCalled());
  const [path, init] = blobMock.mock.calls[0];
  expect(path).toBe("/api/voice/preview");
  expect(JSON.parse(init.body)).toEqual({ blend: { af_heart: 0.75, bf_emma: 0.25, speed: 1 }, text: "Hi, it's Mira. How's your day going?" });
  vi.unstubAllGlobals();
});

test("the built-in persona has no delete button and keeps its name", async () => {
  useUi.setState({ studioPersonaId: "aethel" });
  view();
  expect(await screen.findByRole("heading", { name: "Aethel" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Delete persona" })).toBeNull();
  expect(screen.queryByLabelText("Name")).toBeNull();
});

test("deleting asks first, then returns to the list", async () => {
  personas.push(make("mira", "Mira"));
  useUi.setState({ studioPersonaId: "mira" });
  view();
  await userEvent.click(await screen.findByRole("button", { name: "Delete persona" }));
  await userEvent.click(screen.getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(calls).toContain("DELETE /api/personas/mira"));
  expect(useUi.getState().studioPersonaId).toBeNull();
});

test("talk to starts a conversation with the persona", async () => {
  personas.push(make("mira", "Mira"));
  useUi.setState({ studioPersonaId: "mira" });
  view();
  await userEvent.click(await screen.findByRole("button", { name: /Talk to Mira/ }));
  expect(useUi.getState().screen).toBe("conversation");
  expect(useUi.getState().newChatPersona).toBe("mira");
});

test("style import: pick the speaker, edit the directive, save it with the lines", async () => {
  personas.push(make("rosie", "Rosie"));
  useUi.setState({ studioPersonaId: "rosie" });
  const base = apiMock.getMockImplementation()!;
  apiMock.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/api/personas/rosie/style/import") {
      calls.push(`POST ${path}`);
      const body = JSON.parse(String(init!.body));
      const speakers = [{ name: "Rosie", messages: 20 }, { name: "Sam", messages: 10 }];
      return body.speaker
        ? { speakers, speaker: body.speaker, messages: 20, directive: "Rosie writes in short lowercase messages.", import_id: "imp1" }
        : { speakers, speaker: null, messages: 0, directive: null, import_id: null };
    }
    return base(path, init);
  });
  view();
  const file = new File(["12/05/23, 14:30 - Rosie: lol"], "chat.txt", { type: "text/plain" });
  await userEvent.upload(await screen.findByLabelText("Chat export"), file);
  await userEvent.click(await screen.findByRole("radio", { name: /Rosie/ }));
  const box = await screen.findByLabelText("Style directive");
  await userEvent.clear(box);
  await userEvent.type(box, "Writes in lowercase bursts.");
  await userEvent.click(screen.getByRole("button", { name: "Save style" }));
  await waitFor(() => expect(calls).toContain(
    'PUT /api/personas/rosie/style {"directive":"Writes in lowercase bursts.","import_id":"imp1"}'));
});

test("the persona's accent is applied on <html> and removed again", () => {
  applyPersonaAccent({ light: "#3366cc", dark: "#88aaee" });
  const root = document.documentElement;
  expect(root.dataset.personaAccent).toBe("");
  expect(root.style.getPropertyValue("--accent-light")).toBe("#3366cc");
  expect(root.style.getPropertyValue("--accent-dark")).toBe("#88aaee");
  applyPersonaAccent(null);
  expect(root.dataset.personaAccent).toBeUndefined();
  expect(root.style.getPropertyValue("--accent-light")).toBe("");
});
