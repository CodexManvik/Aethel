import { getBackendInfo } from "./backend";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const { url, token } = await getBackendInfo();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const res = await fetch(`${url}${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await request(path, init);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Binary responses (thumbnails, exports): <img> and <a> can't send the auth header themselves. */
export async function apiBlob(path: string): Promise<Blob> {
  return (await request(path)).blob();
}

/** Resolves once /api/health answers (the backend takes a few seconds to boot). */
export async function waitForBackend(timeoutMs = 60000, intervalMs = 500): Promise<void> {
  const { url } = await getBackendInfo();
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${url}/api/health`);
      if (res.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error("The Aethel backend did not start. Check ~/.aethel/logs/backend.log.");
}
