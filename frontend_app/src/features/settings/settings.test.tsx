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
  auto_approve_skills: true,
  replay_thumbnails: true,
};
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
