import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SettingsView } from "./SettingsView";
import type { AppSettings, ProviderInfo } from "../../lib/types";

const apiMock = vi.hoisted(() => vi.fn());
const saveKeyMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock }));
vi.mock("../../lib/keys", () => ({ saveKey: saveKeyMock, pushStoredKeys: vi.fn() }));

const settings: AppSettings = {
  roles: {
    chat: [{ provider: "groq", model: "llama-3.3-70b-versatile" }],
    agent: [{ provider: "gemini", model: "gemini-2.5-flash" }],
    vision: [{ provider: "gemini", model: "gemini-2.5-flash" }],
  },
  custom_base_url: "",
  private_mode: false,
  internet: false,
  local_llm: { model_path: "", context_size: 0, threads: 4, gpu_layers: 99 },
  temperature: 0.8,
  max_tokens: 1024,
  agent_max_tokens: 8192,
  history_window: 24,
  system1: { enabled: true, auto_tasks: true, intent_threshold: 0.5, stop_threshold: 0.9, skill_threshold: 0.6, judge_threshold: 0.5 },
  memory: { facts_enabled: true, fact_threshold: 0.5, episodic_enabled: true, episodic_min_score: 0.65, facts_k: 6, episodes_k: 3 },
  context_caps: { chat: 16000, agent: 24000 },
  auto_approve_skills: true,
  replay_thumbnails: true,
};
let rebuilds = 0;
const providers: ProviderInfo[] = [
  { id: "groq", label: "Groq", needs_key: true, has_key: true, base_url: "" },
  { id: "gemini", label: "Google Gemini", needs_key: true, has_key: false, base_url: "" },
  { id: "openrouter", label: "OpenRouter", needs_key: true, has_key: false, base_url: "" },
  { id: "custom", label: "Custom (OpenAI-compatible)", needs_key: true, has_key: false, base_url: "" },
  { id: "local", label: "Local (llama.cpp)", needs_key: false, has_key: true, base_url: "" },
];

let patches: unknown[];
beforeEach(() => {
  patches = [];
  apiMock.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/api/settings" && init?.method === "PATCH") {
      const patch = JSON.parse(String(init.body));
      patches.push(patch);
      return { ...settings, ...patch };
    }
    if (path === "/api/settings") return settings;
    if (path === "/api/providers") return providers;
    if (path.endsWith("/models")) return { models: ["m1", "m2"] };
    if (path === "/api/tools") return { servers: { windows: "running", office: "failed: no Office" }, tools: [] };
    if (path === "/api/memory/episodic") return { indexed: 12, exchanges: 14, rebuilding: false };
    if (path === "/api/usage?days=7") return {
      days: 7, by_day: [],
      total: { prompt: 15400, completion: 900, cached: 3000, calls: 9, estimated: false },
      by_purpose: {
        execute: { prompt: 12000, completion: 700, cached: 3000, calls: 6, estimated: false },
        chat_reply: { prompt: 3400, completion: 200, cached: 0, calls: 3, estimated: true },
      },
    };
    if (path === "/api/memory/episodic/rebuild") { rebuilds += 1; return { started: true }; }
    return {};
  });
  saveKeyMock.mockResolvedValue(undefined);
});
afterEach(() => {
  apiMock.mockReset();
  saveKeyMock.mockReset();
});

const renderSettings = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SettingsView />
    </QueryClientProvider>,
  );

test("private mode switch patches settings", async () => {
  renderSettings();
  await userEvent.click(await screen.findByRole("switch", { name: "Private mode" }));
  await waitFor(() => expect(patches).toContainEqual({ private_mode: true }));
});

test("saving a provider key goes through saveKey", async () => {
  renderSettings();
  const row = await screen.findByTestId("key-row-gemini");
  await userEvent.type(within(row).getByLabelText("Google Gemini API key"), "AIza-test");
  await userEvent.click(within(row).getByRole("button", { name: "Save" }));
  expect(saveKeyMock).toHaveBeenCalledWith("gemini", "AIza-test");
});

test("adding a fallback to the chat role and saving sends the whole chain", async () => {
  renderSettings();
  const chat = await screen.findByTestId("role-chat");
  await userEvent.click(within(chat).getByRole("button", { name: "Add fallback" }));
  const models = within(chat).getAllByLabelText("Model");
  await userEvent.clear(models[1]);
  await userEvent.type(models[1], "local");
  await userEvent.selectOptions(within(chat).getAllByLabelText("Provider")[1], "local");
  await userEvent.click(within(chat).getByRole("button", { name: "Save chat models" }));
  await waitFor(() =>
    expect(patches).toContainEqual({
      roles: { chat: [{ provider: "groq", model: "llama-3.3-70b-versatile" }, { provider: "local", model: "local" }] },
    }),
  );
});

test("private mode: no model listing or Test for cloud providers, local still works", async () => {
  const privateSettings: AppSettings = {
    ...settings,
    private_mode: true,
    roles: { ...settings.roles, chat: [{ provider: "groq", model: "g" }, { provider: "local", model: "local" }] },
  };
  apiMock.mockImplementation(async (path: string) => {
    if (path === "/api/settings") return privateSettings;
    if (path === "/api/providers") return providers;
    if (path.endsWith("/models")) return { models: ["m1"] };
    return {};
  });
  renderSettings();
  const chat = await screen.findByTestId("role-chat");
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith("/api/providers/local/models"));
  // groq has a key, but in private mode its models must not be fetched
  expect(apiMock.mock.calls.map(([p]) => p).filter((p: string) => p.endsWith("/models"))).toEqual([
    "/api/providers/local/models",
  ]);
  const [groqTest, localTest] = within(chat).getAllByRole("button", { name: "Test" });
  expect(groqTest).toBeDisabled();
  expect(localTest).toBeEnabled();
});

test("computer control shows each server's connection", async () => {
  renderSettings();
  expect(await screen.findByText("Desktop control")).toBeInTheDocument();
  expect(screen.getByText("Connected")).toBeInTheDocument();
  expect(screen.getByText("Not available: no Office")).toBeInTheDocument();
});

test("learning switches patch settings", async () => {
  renderSettings();
  await userEvent.click(await screen.findByRole("switch", { name: "Start tasks from messages" }));
  await userEvent.click(screen.getByRole("switch", { name: "Use new skills straight away" }));
  await waitFor(() => expect(patches).toEqual([{ system1: { auto_tasks: false } }, { auto_approve_skills: false }]));
});

test("memory switches patch settings and Rebuild starts a rebuild", async () => {
  rebuilds = 0;
  renderSettings();
  await userEvent.click(await screen.findByRole("switch", { name: "Remember facts about me" }));
  await userEvent.click(screen.getByRole("switch", { name: "Recall earlier conversations" }));
  // (the mock merges settings shallowly, so only the first value is meaningful here)
  await waitFor(() => expect(patches.map((p) => Object.keys((p as { memory: object }).memory))).toEqual([["facts_enabled"], ["episodic_enabled"]]));
  expect(patches[0]).toEqual({ memory: { facts_enabled: false } });
  expect(await screen.findByText("12 of 14 exchanges indexed.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Rebuild" }));
  await waitFor(() => expect(rebuilds).toBe(1));
});

test("saving the custom endpoint confirms it, and a bad URL says why", async () => {
  const { ProvidersSection, saveError } = await import("./ProvidersSection");
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ProvidersSection settings={settings} />
    </QueryClientProvider>,
  );
  const saveUrl = () => screen.getAllByRole("button", { name: "Save" }).slice(-1)[0]; // the last Save is the URL's
  expect(saveUrl()).toBeDisabled(); // nothing changed yet
  await userEvent.type(screen.getByLabelText("Custom endpoint URL"), "http://127.0.0.1:1234/v1");
  expect(saveUrl()).toBeEnabled();
  await userEvent.click(saveUrl());
  await waitFor(() => expect(patches).toContainEqual({ custom_base_url: "http://127.0.0.1:1234/v1" }));
  expect(saveError(new Error('[{"msg":"Value error, must start with http:// or https://, e.g. http://127.0.0.1:1234/v1"}]')))
    .toBe("That URL doesn't look right: it must start with http:// or https://, e.g. http://127.0.0.1:1234/v1.");
  expect(saveError(new Error("boom"))).toBe("Couldn't save: boom");
});

test("usage lists the last 7 days by purpose, biggest first, with a total", async () => {
  renderSettings();
  const table = await screen.findByRole("table");
  const rows = within(table).getAllByRole("row").slice(1).map((r) => r.textContent);
  expect(rows).toEqual(["Doing tasks12.0k7003.0k6", "Replies≈3.4k20003", "Total15.4k9003.0k9"]);
});

test("the Background role is listed with the other models", async () => {
  renderSettings();
  expect(await screen.findByText("Background")).toBeInTheDocument();
  expect(screen.getByText(/Remembering facts and other housekeeping/)).toBeInTheDocument();
});
