import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock }));
import { BrowserSection, describeBrowser } from "./BrowserSection";
import type { AppSettings } from "../../lib/types";

const settings = (show = false) => ({ browser: { show } }) as AppSettings;
let info: { status: string; show: boolean; profile_exists: boolean; signing_in: boolean };
let calls: string[];

beforeEach(() => {
  info = { status: "running", show: false, profile_exists: true, signing_in: false };
  calls = [];
  apiMock.mockImplementation(async (path: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${path}${init?.body ? " " + init.body : ""}`);
    if (path === "/api/browser") return { ...info };
    if (path === "/api/settings") return { ...settings(), ...JSON.parse(String(init?.body ?? "{}")) };
    return {};
  });
});

const view = (s = settings()) =>
  render(<QueryClientProvider client={new QueryClient()}><BrowserSection settings={s} /></QueryClientProvider>);

test("the status says in plain words whether the browser is ready", async () => {
  view();
  expect(await screen.findByText(/Ready\. Edge opens in the background/)).toBeInTheDocument();
  expect(describeBrowser("absent")).toMatch(/needs Node\.js/);
  expect(describeBrowser("failed: npx exited")).toBe("Not available: npx exited");
  expect(describeBrowser("starting")).toMatch(/Starting/);
});

test("Show browser patches its own setting, and a restart is offered only once it differs from what's running", async () => {
  const { rerender } = view();
  await screen.findByText(/Ready/);
  expect(screen.queryByRole("button", { name: "Restart now" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("switch", { name: "Show browser" }));
  await waitFor(() => expect(calls).toContain('PATCH /api/settings {"browser":{"show":true}}'));
  rerender(<QueryClientProvider client={new QueryClient()}><BrowserSection settings={settings(true)} /></QueryClientProvider>);
  await userEvent.click(await screen.findByRole("button", { name: "Restart now" }));
  await waitFor(() => expect(calls).toContain("POST /api/browser/restart"));
});

test("Sign in opens the window, and the button says so while it's open", async () => {
  view();
  await userEvent.click(await screen.findByRole("button", { name: "Sign in…" }));
  await waitFor(() => expect(calls).toContain("POST /api/browser/sign-in"));
  info.signing_in = true;
  expect(await screen.findByRole("button", { name: "Sign-in window open" }, { timeout: 6000 })).toBeDisabled();
}, 10000);

test("clearing the data asks first, and Cancel changes nothing", async () => {
  view();
  await userEvent.click(await screen.findByRole("button", { name: "Clear browser data…" }));
  const ask = screen.getByRole("group", { name: "Confirm clearing browser data" });
  await userEvent.click(within(ask).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("group", { name: "Confirm clearing browser data" })).not.toBeInTheDocument();
  expect(calls.some((c) => c.startsWith("DELETE"))).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Clear browser data…" }));
  await userEvent.click(within(screen.getByRole("group", { name: "Confirm clearing browser data" })).getByRole("button", { name: "Yes, clear it" }));
  await waitFor(() => expect(calls).toContain("DELETE /api/browser/profile"));
  await waitFor(() => expect(screen.queryByRole("group", { name: "Confirm clearing browser data" })).not.toBeInTheDocument());
});

test("with nothing saved there is nothing to clear, and without a browser nothing can be started", async () => {
  info = { status: "absent", show: false, profile_exists: false, signing_in: false };
  view();
  expect(await screen.findByRole("button", { name: "Nothing saved yet" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Sign in…" })).toBeDisabled();
});

test("a refusal from the server doesn't break the section", async () => {
  apiMock.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/api/browser" && !init) return { ...info };
    throw new Error("A task is using the browser right now.");
  });
  view();
  await userEvent.click(await screen.findByRole("button", { name: "Sign in…" }));
  expect(await screen.findByRole("button", { name: "Sign in…" })).toBeEnabled();   // still there, usable again
});
