import { render, screen } from "@testing-library/react";
import { App } from "./App";

vi.mock("./lib/api", async (orig) => ({
  ...(await orig<typeof import("./lib/api")>()),
  waitForBackend: () => new Promise(() => {}),
}));

test("shows the waking-up screen while the backend boots", () => {
  render(<App />);
  expect(screen.getByText("Aethel")).toBeInTheDocument();
  expect(screen.getByText(/waking up/i)).toBeInTheDocument();
});
