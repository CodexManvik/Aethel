import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const apiMock = vi.hoisted(() => vi.fn());
const saveKeyMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock }));
vi.mock("../../lib/keys", () => ({ saveKey: saveKeyMock }));
import { VoiceSection, describeVoice } from "./VoiceSection";
import type { AppSettings } from "../../lib/types";

const voice = { stt: "azure", stt_model: "small", language: "en-GB", azure_region: "", tts: "kokoro", azure_voice: "en-GB-SoniaNeural",
  max_wait_ms: 1500, ack: true, narration: true, default_blend: { af_heart: 1, speed: 1 } };
const settings = (patch = {}) => ({ voice: { ...voice, ...patch } }) as unknown as AppSettings;
let calls: string[];
let status: { stt: { name: string; azure: boolean; loaded: boolean }; tts: { kokoro: boolean; voices: number; azure: boolean }; last_turn_ms: number | null };
let keys: Record<string, boolean>;

beforeEach(() => {
  calls = [];
  keys = { azure_speech: false };
  status = { stt: { name: "local:faster-whisper-small", azure: false, loaded: false },
    tts: { kokoro: true, voices: 54, azure: false }, last_turn_ms: null };
  saveKeyMock.mockReset().mockResolvedValue(undefined);
  apiMock.mockImplementation(async (path: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${path}${init?.body ? " " + init.body : ""}`);
    if (path === "/api/voice/status") return status;
    if (path === "/api/keys/status") return keys;
    if (path === "/api/voice/voices") return { "en-us": ["af_heart", "am_adam"], "en-gb": ["bf_emma"] };
    if (path === "/api/voice/azure-check") return { ok: true, message: "Azure Speech works in uksouth." };
    if (path === "/api/settings") return { ...settings(), ...JSON.parse(String(init?.body ?? "{}")) };
    return {};
  });
});

const view = (s = settings()) =>
  render(<QueryClientProvider client={new QueryClient()}><VoiceSection settings={s} /></QueryClientProvider>);

test("the status says who hears you and how the last call went", async () => {
  view();
  expect(await screen.findByText(/Hears you on this PC \(the model loads when a call starts\)/)).toBeInTheDocument();
  expect(describeVoice({ ...status, stt: { name: "azure:uksouth", azure: true, loaded: true }, last_turn_ms: 1432 }))
    .toBe("Hears you with Azure Speech (uksouth). Last call: 1.4 s to the first sound.");
});

test("the key is saved through the credential store and never shown back", async () => {
  view();
  await userEvent.type(await screen.findByLabelText("Azure Speech key"), "  my-key  ");
  await userEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);
  await waitFor(() => expect(saveKeyMock).toHaveBeenCalledWith("azure_speech", "my-key"));
  expect(screen.getByLabelText("Azure Speech key")).toHaveValue("");
  expect(screen.getByLabelText("Azure Speech key")).toHaveAttribute("type", "password");
});

test("a saved key can be removed, and Check needs both the key and a region", async () => {
  keys = { azure_speech: true };
  view(settings({ azure_region: "uksouth" }));
  const check = await screen.findByRole("button", { name: "Check" });
  await waitFor(() => expect(check).toBeEnabled());
  await userEvent.click(check);
  await waitFor(() => expect(calls).toContain("POST /api/voice/azure-check"));
  await userEvent.click(screen.getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(saveKeyMock).toHaveBeenCalledWith("azure_speech", null));
});

test("Check is off without a region, and the region is saved as lower case under voice", async () => {
  keys = { azure_speech: true };
  view();
  expect(await screen.findByRole("button", { name: "Check" })).toBeDisabled();
  await userEvent.type(screen.getByLabelText("Azure region"), " UKSouth ");
  await userEvent.click(screen.getAllByRole("button", { name: "Save" })[1]);
  await waitFor(() => expect(calls.some((c) => c.startsWith("PATCH /api/settings") && c.includes('"azure_region":"uksouth"'))).toBe(true));
});

test("the engines are chosen with plain words", async () => {
  view();
  await userEvent.selectOptions(await screen.findByLabelText("Hear me with"), "local");
  await userEvent.selectOptions(screen.getByLabelText("Speak with"), "azure");
  await waitFor(() => {
    expect(calls.some((c) => c.includes('"stt":"local"'))).toBe(true);
    expect(calls.some((c) => c.includes('"tts":"azure"'))).toBe(true);
  });
});


test("picking a voice replaces the blend, and the switches save under voice", async () => {
  view();
  await screen.findByRole("option", { name: "bf_emma" });
  await userEvent.selectOptions(screen.getByLabelText("Aethel's voice"), "bf_emma");
  await userEvent.click(screen.getByRole("switch", { name: "Short acknowledgements" }));
  await userEvent.click(screen.getByRole("switch", { name: "Talk me through tasks" }));
  await waitFor(() => {
    expect(calls.some((c) => c.includes('"default_blend":{"bf_emma":1,"speed":1}'))).toBe(true);
    expect(calls.some((c) => c.includes('"ack":false'))).toBe(true);
    expect(calls.some((c) => c.includes('"narration":false'))).toBe(true);
  });
});
