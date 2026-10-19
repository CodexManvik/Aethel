import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const apiMock = vi.hoisted(() => vi.fn());
const saveKeyMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock }));
vi.mock("../../lib/keys", async (orig) => ({ ...(await orig<typeof import("../../lib/keys")>()), saveKey: saveKeyMock }));
import { describeImages, ImagesSection } from "./ImagesSection";
import type { AppSettings } from "../../lib/types";

const settings = { images: { backends: ["gemini", "openai", "comfyui", "openrouter", "local"], gemini_model: "gemini-3.1-flash-image",
  openrouter_model: "", identity_threshold: 0.45, request_threshold: 0.6, openai: { base_url: "", model: "" },
  comfyui: { base_url: "", workflow_path: "" }, local_dir: "" } } as unknown as AppSettings;
let patches: unknown[];

beforeEach(() => {
  patches = [];
  saveKeyMock.mockReset().mockResolvedValue(undefined);
  apiMock.mockReset().mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/api/images/status") return { backend: null, why_not: "x", face_models: false, downloading: false, progress: 0, error: null };
    if (path === "/api/images/comfyui/check") {
      const { path: p } = JSON.parse(String(init?.body));
      return p.endsWith("good.json") ? { ok: true, placeholders: ["prompt", "seed"], error: null }
        : { ok: false, placeholders: [], error: "The workflow file isn't JSON." };
    }
    if (path === "/api/settings") patches.push(JSON.parse(String(init?.body)));
    return {};
  });
});

const view = () => render(<QueryClientProvider client={new QueryClient()}><ImagesSection settings={settings} /></QueryClientProvider>);

test("an OpenAI-compatible image server is saved with its key", async () => {
  view();
  await userEvent.type(screen.getByLabelText("Image server URL"), "http://127.0.0.1:8080/v1");
  await userEvent.type(screen.getByLabelText("Image server model"), "flux");
  await userEvent.type(screen.getByLabelText("Image server key"), "sk-1");
  await userEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);
  await waitFor(() => expect(patches).toContainEqual({ images: { openai: { base_url: "http://127.0.0.1:8080/v1", model: "flux" } } }));
  expect(saveKeyMock).toHaveBeenCalledWith("endpoint:images-openai", "sk-1");
});

test("a ComfyUI workflow is checked and saved", async () => {
  view();
  await userEvent.type(screen.getByLabelText("ComfyUI URL"), "http://127.0.0.1:8188");
  await userEvent.type(screen.getByLabelText("ComfyUI workflow"), "D:\\bad.json");
  await userEvent.click(screen.getByRole("button", { name: "Check" }));
  expect(await screen.findByRole("status")).toHaveTextContent("The workflow file isn't JSON.");
  await userEvent.clear(screen.getByLabelText("ComfyUI workflow"));
  await userEvent.type(screen.getByLabelText("ComfyUI workflow"), "D:\\good.json");
  await userEvent.click(screen.getByRole("button", { name: "Check" }));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Uses {{prompt}}, {{seed}} (text only: faces may vary)."));
  await userEvent.click(screen.getAllByRole("button", { name: "Save" })[1]);
  await waitFor(() => expect(patches).toContainEqual({ images: { comfyui: { base_url: "http://127.0.0.1:8188", workflow_path: "D:\\good.json" } } }));
});

test("the local folder is saved; no Browse in browser dev", async () => {
  view();
  expect(screen.queryByRole("button", { name: "Browse…" })).toBeNull();
  await userEvent.type(screen.getByLabelText("Local model folder"), "D:\\z");
  await userEvent.click(screen.getAllByRole("button", { name: "Save" })[2]);
  await waitFor(() => expect(patches).toContainEqual({ images: { local_dir: "D:\\z" } }));
});

test("the status names the backend in words", () => {
  expect(describeImages({ backend: "comfyui:aethel-api", why_not: "", face_models: true }))
    .toBe("Photos are made with ComfyUI: aethel-api. The face check is installed.");
  expect(describeImages({ backend: "openai:flux", why_not: "", face_models: false }))
    .toBe("Photos are made with your image server: flux. The face check isn't installed (Personas → Face).");
});
