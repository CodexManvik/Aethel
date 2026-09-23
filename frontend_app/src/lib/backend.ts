export interface BackendInfo {
  url: string;
  token: string | null;
}

export function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

let cached: Promise<BackendInfo> | null = null;

export function getBackendInfo(): Promise<BackendInfo> {
  if (!cached) {
    cached = (async () => {
      if (inTauri()) {
        const { invoke } = await import("@tauri-apps/api/core");
        return invoke<BackendInfo>("get_backend_info");
      }
      return {
        url: import.meta.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8765",
        token: import.meta.env.VITE_AETHEL_TOKEN ?? null,
      };
    })();
  }
  return cached;
}

export function resetBackendInfo() {
  cached = null;
}
