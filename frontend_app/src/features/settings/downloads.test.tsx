import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock }));
import { DownloadButton } from "./Downloads";

const item = (patch = {}) => ({ installed: false, running: false, progress: 0, error: null, ...patch });
let state: Record<string, unknown>;
let calls: string[];

beforeEach(() => {
  calls = [];
  state = { voices: item(), "llama-vulkan": item(), "llama-cpu": item(), "local-images": item(), packages_installable: false };
  apiMock.mockReset().mockImplementation(async (path: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${path}`);
    if (path === "/api/downloads") return state;
    state = { ...state, voices: item({ running: true, progress: 0.4 }) }; // the server starts it at once
    return { started: true };
  });
});

const wrap = (ui: React.ReactNode) => render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);

test("starts a download, shows progress, and calls onDone once when it finishes", async () => {
  const onDone = vi.fn();
  wrap(<DownloadButton name="voices" label="Download the voices" done="Installed" onDone={onDone} />);
  await userEvent.click(await screen.findByRole("button", { name: "Download the voices" }));
  expect(calls).toContain("POST /api/downloads/voices");
  expect(await screen.findByRole("progressbar", { name: "Download the voices" }, { timeout: 3000 })).toHaveAttribute("aria-valuenow", "40");
  state = { ...state, voices: item({ installed: true }) };
  expect(await screen.findByText("Installed", undefined, { timeout: 3000 })).toBeInTheDocument();
  await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
});

test("an error is shown and the button comes back", async () => {
  state = { ...state, "llama-cpu": item({ error: "llama-b11381-bin-win-cpu-x64.zip didn't match its pinned SHA-256; refused." }) };
  wrap(<DownloadButton name="llama-cpu" label="CPU only (19 MB)" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("pinned SHA-256");
  expect(screen.getByRole("button", { name: "CPU only (19 MB)" })).toBeEnabled();
});
