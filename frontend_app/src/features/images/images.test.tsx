import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const apiMock = vi.hoisted(() => vi.fn());
const blobMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock, apiBlob: blobMock }));
import { applyEvent, type SessionData, type UiPhoto } from "../../stores/session";
import { Faces } from "../personas/Faces";
import { Gallery } from "../personas/Gallery";
import type { Persona } from "../personas/personasApi";
import { DevelopingPhoto, expectedMs, recordDuration } from "./DevelopingPhoto";

const persona = (extra: Partial<Persona> = {}): Persona => ({ id: "mira", name: "Mira", built_in: false, anchors: [], sheet: [],
  accent: null, voice: { blend: { af_heart: 1 }, speed: 1 }, image_style: "film", image_frequency: "on_request",
  tone_engine: true, created: null, sections: {}, ...extra });
let calls: string[];

beforeEach(() => {
  calls = [];
  localStorage.clear();
  document.documentElement.dataset.motion = "full";
  URL.createObjectURL = vi.fn(() => "blob:photo");
  URL.revokeObjectURL = vi.fn();
  blobMock.mockReset().mockResolvedValue(new Blob(["jpg"]));
  apiMock.mockReset().mockImplementation(async (path: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${path}${init?.body ? " " + init.body : ""}`);
    return {};
  });
});

const wrap = (ui: React.ReactNode) => render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);
const photo = (patch: Partial<UiPhoto> = {}): UiPhoto => ({ id: "img1", personaId: "mira", status: "pending",
  caption: "on the beach at sunset", ...patch });

test("a photo develops, then comes into focus", async () => {
  const { rerender } = render(<DevelopingPhoto photo={photo()} />);
  expect(screen.getByRole("img", { name: "A photo is developing" })).toBeInTheDocument();
  expect(screen.getByText("on the beach at sunset")).toBeInTheDocument();
  rerender(<DevelopingPhoto photo={photo({ status: "ready", url: "/api/personas/mira/files/gallery/img1.jpg", startedAt: Date.now() - 8000 })} />);
  expect(await screen.findByRole("img", { name: "on the beach at sunset" })).toHaveAttribute("src", "blob:photo");
  expect(blobMock).toHaveBeenCalledWith("/api/personas/mira/files/gallery/img1.jpg");
  expect(expectedMs()).toBeGreaterThan(7000); // the time it took is remembered
});

test("a failed photo says so quietly and can be tried again", async () => {
  render(<DevelopingPhoto photo={photo({ status: "failed", error: "busy" })} />);
  expect(screen.getByText("The photo didn't come out.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(calls).toContain("POST /api/images/img1/retry");
});

test("a retry that fails again can be tried again; an interrupted one can't", async () => {
  const { rerender } = render(<DevelopingPhoto photo={photo({ status: "failed" })} />);
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(screen.getByRole("button", { name: "Try again" })).toBeDisabled();
  rerender(<DevelopingPhoto photo={photo({ status: "pending" })} />);
  rerender(<DevelopingPhoto photo={photo({ status: "failed" })} />);
  expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  rerender(<DevelopingPhoto photo={photo({ id: "img2", status: "failed", stale: true })} />);
  expect(screen.getByText("The photo was interrupted and can't be redone.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
});

test("re-reading the conversation keeps a photo that finished meanwhile", async () => {
  const { useSession } = await import("../../stores/session");
  useSession.getState().setConversation("c1", [{ id: "m1", role: "assistant", content: "hi", status: "complete",
    images: [{ id: "img1", personaId: "mira", status: "ready", caption: "beach", url: "/x.jpg", score: 0.8 }] }]);
  useSession.getState().replaceMessages("c1", [{ id: "m1", role: "assistant", content: "hi", status: "complete",
    images: [{ id: "img1", personaId: "mira", status: "pending", caption: "beach" }] }]);
  expect(useSession.getState().messages[0].images?.[0].status).toBe("ready");
});

test("the card takes the stored average time to develop", () => {
  expect(expectedMs()).toBe(12000);
  recordDuration(20000);
  recordDuration(10000);
  expect(expectedMs()).toBe(17000);
  render(<DevelopingPhoto photo={photo()} />);
  expect(screen.getByTestId("photo-pending")).toHaveAttribute("data-duration", "17000");
});

test("reduced motion: no blur, only opacity", () => {
  document.documentElement.dataset.motion = "reduced";
  render(<DevelopingPhoto photo={photo()} />);
  const card = screen.getByRole("img", { name: "A photo is developing" });
  expect(card.style.filter).toBe("");
  expect(card.style.transition).toBe("");
});

test("photo events land on their reply, from pending to ready", () => {
  let data: SessionData = { conversationId: "c1", streamingId: "m1", notices: [], socketStatus: "open", pendingSources: {},
    messages: [{ id: "m1", role: "assistant", content: "", status: "streaming" }] };
  const base = { image_id: "img1", persona_id: "mira", conversation_id: "c1", message_id: "m1" };
  data = applyEvent(data, { type: "image_pending", ...base, caption: "beach" });
  expect(data.messages[0].images?.[0]).toMatchObject({ status: "pending", caption: "beach" });
  data = applyEvent(data, { type: "image_ready", ...base, url: "/x.jpg", identity_score: 0.81 });
  expect(data.messages[0].images).toEqual([expect.objectContaining({ status: "ready", url: "/x.jpg", score: 0.81, caption: "beach" })]);
  data = applyEvent(data, { type: "image_failed", ...base, message_id: "elsewhere", message: "x", retryable: true });
  expect(data.messages[0].images?.[0].status).toBe("ready");
});

const item = (id: string, reference = false, extra = {}) => ({ id, url: `/api/personas/mira/files/gallery/${id}.jpg`,
  scene: `scene ${id}`, created: "2026-10-19T10:00:00Z", score: 0.8, low: false, reference, reason: "chat", ...extra });

test("the gallery's reference toggle respects the cap of six", async () => {
  const items = [item("a", true), item("b", true), item("c")];
  apiMock.mockImplementation(async (path: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${path}${init?.body ? " " + init.body : ""}`);
    if (path === "/api/personas/mira/gallery") return [...items, item("s", false, { reason: "studio" })];
    return {};
  });
  // anchor + 3 sheet + 2 pooled = 6: a third can't be added, a pooled one can be taken out
  wrap(<Gallery persona={persona({ anchors: ["portraits/anchor.jpg"], sheet: ["a", "b", "c"].map((s) => `portraits/${s}.jpg`) })} />);
  expect(await screen.findAllByRole("button", { name: /scene/ })).toHaveLength(3); // studio pictures aren't memories
  await userEvent.click(screen.getByRole("button", { name: "scene c" }));
  expect(await screen.findByRole("checkbox", { name: "Use as reference" })).toBeDisabled();
  expect(screen.getByText("6/6 references")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  await userEvent.click(screen.getByRole("button", { name: "scene a" }));
  const box = await screen.findByRole("checkbox", { name: "Use as reference" });
  expect(box).toBeEnabled();
  await userEvent.click(box);
  await waitFor(() => expect(calls).toContain('POST /api/personas/mira/gallery/a/reference {"on":false}'));
});

test("picking one of four faces makes it the anchor", async () => {
  apiMock.mockImplementation(async (path: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${path}${init?.body ? " " + init.body : ""}`);
    if (path === "/api/images/status") return { backend: "gemini:x", why_not: "", face_models: true, downloading: false, progress: 1, error: null };
    if (path === "/api/personas/mira/portraits") {
      return [1, 2, 3, 4].map((n) => ({ id: `f${n}`, ok: true, url: `/api/personas/mira/files/gallery/f${n}.jpg`, score: null }));
    }
    return persona({ anchors: ["portraits/anchor.jpg"] });
  });
  wrap(<Faces persona={persona()} />);
  await userEvent.click(await screen.findByRole("button", { name: "Generate 4 faces" }));
  await userEvent.click(await screen.findByRole("button", { name: "Face 3" }));
  await waitFor(() => expect(calls).toContain('POST /api/personas/mira/anchor {"image_id":"f3"}'));
});

test("the face check is downloaded only when asked", async () => {
  apiMock.mockImplementation(async (path: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${path}`);
    if (path === "/api/images/status") return { backend: null, why_not: "Add a Gemini key in Settings → Providers to make photos.",
      face_models: false, downloading: false, progress: 0, error: null };
    return {};
  });
  wrap(<Faces persona={persona()} />);
  expect(await screen.findByText(/Add a Gemini key/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Generate 4 faces" })).toBeDisabled();
  expect(calls).not.toContain("POST /api/images/face-models");
  await userEvent.click(screen.getByRole("button", { name: "Download the face check" }));
  expect(calls).toContain("POST /api/images/face-models");
});
