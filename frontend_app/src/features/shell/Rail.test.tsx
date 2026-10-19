import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock }));
import { Rail } from "./Rail";
import { useSession } from "../../stores/session";
import { useTone } from "../../stores/tone";
import { useUi } from "../../stores/ui";

const persona = (id: string, name: string, built_in = false) => ({ id, name, built_in, anchors: [], sheet: [], accent: null,
  voice: { blend: { af_heart: 1 }, speed: 1 }, image_style: "", image_frequency: "on_request", tone_engine: !built_in,
  created: null, sections: {} });

beforeEach(() => {
  useUi.setState({ screen: "conversation", threadsOpen: false, theme: "light", motion: "system", newChatPersona: "aethel" });
  useSession.getState().setConversation(null, []);
  apiMock.mockReset().mockImplementation(async (path: string) =>
    path === "/api/personas" ? [persona("aethel", "Aethel", true), persona("mira", "Mira")] : []);
});

const view = () => render(<QueryClientProvider client={new QueryClient()}><Rail /></QueryClientProvider>);

test("settings button switches screen and back", async () => {
  view();
  await userEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(useUi.getState().screen).toBe("settings");
  await userEvent.click(screen.getByRole("button", { name: "Aethel" }));
  expect(useUi.getState().screen).toBe("conversation");
});

test("threads button toggles the threads panel", async () => {
  view();
  await userEvent.click(screen.getByRole("button", { name: "Conversations" }));
  expect(useUi.getState().threadsOpen).toBe(true);
});

test("theme toggle flips data-theme on <html>", async () => {
  view();
  await userEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(useUi.getState().theme).toBe("dark");
});

test("the rail lists the personas, built-in first, and a click starts a conversation with one", async () => {
  useSession.getState().setConversation("c1", []);
  view();
  const mira = await screen.findByRole("button", { name: "Mira" });
  const names = screen.getAllByRole("button").map((b) => b.getAttribute("aria-label"));
  expect(names.indexOf("Aethel")).toBeLessThan(names.indexOf("Mira"));
  await userEvent.click(mira);
  expect(useUi.getState().newChatPersona).toBe("mira");
  expect(useSession.getState().conversationId).toBeNull();
  await waitFor(() => expect(screen.getByRole("button", { name: "Mira" })).toHaveAttribute("aria-current", "true"));
});

test("the active companion glows with the conversation's energy; the studio opens from the rail", async () => {
  apiMock.mockImplementation(async (path: string) =>
    path === "/api/personas" ? [persona("aethel", "Aethel", true), persona("mira", "Mira")]
      : path === "/api/conversations" ? [{ id: "c1", title: "", persona_id: "mira", created_at: "", updated_at: "" }] : []);
  useTone.setState({ byConversation: { c1: { mood: 0.5, energy: 1, label: "Enthusiastic" } } });
  useSession.getState().setConversation("c1", []);
  view();
  await waitFor(() => expect(screen.getByTestId("avatar-mira").style.boxShadow).toContain("55%"));
  expect(screen.getByTestId("avatar-aethel").style.boxShadow).not.toContain("color-mix");
  await userEvent.click(screen.getByRole("button", { name: "Personas" }));
  expect(useUi.getState().screen).toBe("personas");
  expect(useUi.getState().studioPersonaId).toBeNull();
});
