import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock }));
import { describeLocal, LocalModelSection } from "./LocalModelSection";
import type { AppSettings } from "../../lib/types";

const settings = { local_llm: { model_path: "", server_path: "", context_size: 0, threads: 4, gpu_layers: 99 } } as unknown as AppSettings;
let patches: unknown[];

beforeEach(() => {
  patches = [];
  apiMock.mockReset().mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/api/providers/local/status") {
      return { model: null, mmproj: null, server: null, running: false, why_not: "No .gguf in C:\\models\\llm; choose one." };
    }
    if (path === "/api/settings") patches.push(JSON.parse(String(init?.body)));
    return {};
  });
});

test("it says why the local model can't start, and saves both paths", async () => {
  render(<QueryClientProvider client={new QueryClient()}><LocalModelSection settings={settings} /></QueryClientProvider>);
  expect(await screen.findByText(/No .gguf in/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Browse…" })).toBeNull(); // browser dev: paths are typed
  await userEvent.type(screen.getByLabelText("Model file"), "D:\\m\\q.gguf");
  await userEvent.type(screen.getByLabelText("llama-server"), "D:\\llama\\llama-server.exe");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(patches).toContainEqual({ local_llm: expect.objectContaining({
    model_path: "D:\\m\\q.gguf", server_path: "D:\\llama\\llama-server.exe" }) }));
});

test("describes what it would run", () => {
  expect(describeLocal({ model: "D:\\m\\gemma.gguf", mmproj: "D:\\m\\mmproj.gguf", server: "D:\\l\\llama-server.exe",
    running: true, why_not: null })).toBe("Running: gemma.gguf (with vision) with D:\\l\\llama-server.exe.");
});
