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

async function tauriInvoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(command, args);
}

/** Desktop only: why the backend process stopped, or null while it's running. */
export async function backendExited(): Promise<string | null> {
  return inTauri() ? tauriInvoke<string | null>("backend_exited") : null;
}

/** Desktop only: the end of ~/.aethel/logs/backend.log. */
export async function backendLogTail(lines = 60): Promise<string | null> {
  return inTauri() ? tauriInvoke<string>("backend_log_tail", { lines }) : null;
}

export async function openBackendLogs(): Promise<void> {
  if (inTauri()) await tauriInvoke("open_backend_logs");
}

export async function restartBackend(): Promise<void> {
  if (inTauri()) await tauriInvoke("restart_backend");
}

/** How the installed app's first-run setup is going (its own Python and packages, spec 2026-10-19 §I2). */
export interface SetupStatus {
  phase: "checking" | "installing" | "ready" | "failed";
  step: string;
  done: number;
  total: number;
  error: string | null;
}

/** Desktop only; a dev build and the browser are always "ready" (they use the repo's backend). */
export async function setupStatus(): Promise<SetupStatus> {
  if (!inTauri()) return { phase: "ready", step: "", done: 0, total: 0, error: null };
  return tauriInvoke<SetupStatus>("setup_status");
}

/** The end of ~/.aethel/logs/setup.log (empty outside the installed app). */
export async function setupLogTail(): Promise<string | null> {
  return inTauri() ? tauriInvoke<string>("setup_log_tail") : null;
}
