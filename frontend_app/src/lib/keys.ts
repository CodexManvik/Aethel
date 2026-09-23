import { api } from "./api";
import { inTauri } from "./backend";

export type KeyProvider = "groq" | "gemini" | "openrouter" | "custom";

/** On startup: copy keys from Windows Credential Manager into backend memory. */
export async function pushStoredKeys(): Promise<void> {
  if (!inTauri()) return; // browser dev: use env vars (GROQ_API_KEY etc.) on the backend
  const { invoke } = await import("@tauri-apps/api/core");
  const all = await invoke<Record<string, string>>("secret_get_all");
  const keys = Object.fromEntries(Object.entries(all).filter(([p]) => p !== "typesafe"));
  if (Object.keys(keys).length) {
    await api("/api/keys", { method: "POST", body: JSON.stringify({ keys }) });
  }
}

/** Save (or with null, remove) a key: OS credential store + backend memory. */
export async function saveKey(provider: KeyProvider, value: string | null): Promise<void> {
  if (inTauri()) {
    const { invoke } = await import("@tauri-apps/api/core");
    if (value) await invoke("secret_set", { provider, value });
    else await invoke("secret_delete", { provider });
  }
  await api("/api/keys", { method: "POST", body: JSON.stringify({ keys: { [provider]: value } }) });
}
