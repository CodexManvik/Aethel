import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Boot } from "./Boot";

const backend = vi.hoisted(() => ({
  inTauri: vi.fn(() => true),
  backendExited: vi.fn(),
  backendLogTail: vi.fn(),
  openBackendLogs: vi.fn(async () => {}),
  restartBackend: vi.fn(async () => {}),
}));
vi.mock("../../lib/backend", () => backend);
vi.mock("../../lib/api", () => ({ waitForBackend: () => new Promise(() => {}) }));  // never answers
vi.mock("../../lib/keys", () => ({ pushStoredKeys: async () => {} }));

beforeEach(() => {
  backend.backendExited.mockResolvedValue("it stopped (exit code: 1)");
  backend.backendLogTail.mockResolvedValue("python: C:\miniconda3\python.exe (found via python on PATH)\nModuleNotFoundError: No module named 'openai'");
});

test("a backend that dies is reported at once, with the end of its log and ways to act on it", async () => {
  render(<Boot><p>app</p></Boot>);
  expect(await screen.findByRole("alert")).toHaveTextContent("The Aethel backend didn't start: it stopped (exit code: 1).");
  expect(screen.getByLabelText("End of the backend log")).toHaveTextContent("No module named 'openai'");
  await userEvent.click(screen.getByRole("button", { name: "Open log folder" }));
  expect(backend.openBackendLogs).toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Copy log" })).toBeInTheDocument();
  backend.backendExited.mockResolvedValue(null);
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(backend.restartBackend).toHaveBeenCalled();
  expect(await screen.findByText("waking up…")).toBeInTheDocument();
});
