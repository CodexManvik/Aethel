import { api } from "./api";
import { inTauri } from "./backend";
import type { AppSettings } from "./types";

/** A built-in provider's key, or a user endpoint's ("endpoint:<id>"; "custom" for the one from before endpoints). */
export type KeyProvider = "groq" | "gemini" | "openrouter" | "custom" | "azure_speech" | `endpoint:${string}`;

export const IMAGES_OPENAI_KEY = "endpoint:images-openai" as const;

export const endpointKey = (id: string): KeyProvider => (id === "custom" ? "custom" : `endpoint:${id}`);

/** On startup: copy keys from Windows Credential Manager into backend memory. The store can't list keys, so the
 * endpoints' are asked for by name (from the saved settings). */
export async function pushStoredKeys(): Promise<void> {
  if (!inTauri()) return; // browser dev: use env vars (GROQ_API_KEY etc.) on the backend
  const { invoke } = await import("@tauri-apps/api/core");
  const all = await invoke<Record<string, string>>("secret_get_all");
  let named: Record<string, string> = {};
  try {
    const settings = await api<AppSettings>("/api/settings");
    const names = [...(settings.endpoints ?? []).filter((e) => e.id !== "custom").map((e) => `endpoint:${e.id}`), IMAGES_OPENAI_KEY];
    named = await invoke<Record<string, string>>("secret_get_many", { providers: names });
  } catch {
    /* the built-in keys still go through */
  }
  const keys = Object.fromEntries(Object.entries({ ...all, ...named }).filter(([p]) => p !== "typesafe"));
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
