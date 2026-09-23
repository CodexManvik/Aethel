import { api, ApiError } from "./api";
import { resetBackendInfo } from "./backend";

beforeEach(() => resetBackendInfo());

test("sends JSON with the bearer token and parses the response", async () => {
  vi.stubEnv("VITE_AETHEL_TOKEN", "tok");
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: 1 }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  const out = await api<{ ok: number }>("/api/x", { method: "POST", body: JSON.stringify({ a: 1 }) });
  expect(out).toEqual({ ok: 1 });
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("http://127.0.0.1:8765/api/x");
  expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok");
  expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

test("throws ApiError with the backend detail", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "nope" }), { status: 404 })));
  await expect(api("/api/y")).rejects.toEqual(new ApiError(404, "nope"));
  vi.unstubAllGlobals();
});
