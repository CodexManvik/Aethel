import { create } from "zustand";

export type Screen = "conversation" | "memory" | "tasks" | "settings";
export type ThemePref = "light" | "dark" | "system";
export type MotionPref = "system" | "full" | "reduced";

const THEME_KEY = "aethel_theme";
const MOTION_KEY = "aethel_motion";

function read<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = localStorage.getItem(key);
    return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable */
  }
}

export function resolveTheme(pref: ThemePref): "light" | "dark" {
  if (pref !== "system") return pref;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function resolveMotion(pref: MotionPref): "full" | "reduced" {
  if (pref !== "system") return pref;
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "reduced" : "full";
}

function applyAppearance(theme: ThemePref, motion: MotionPref) {
  const root = document.documentElement;
  root.dataset.theme = resolveTheme(theme);
  root.dataset.motion = resolveMotion(motion);
}

const storedTheme = () => read(THEME_KEY, ["light", "dark", "system"] as const, "system");
const storedMotion = () => read(MOTION_KEY, ["system", "full", "reduced"] as const, "system");

export function applyStoredAppearance() {
  applyAppearance(storedTheme(), storedMotion());
}

interface UiState {
  screen: Screen;
  threadsOpen: boolean;
  theme: ThemePref;
  motion: MotionPref;
  taskPanelId: string | null;
  setScreen(screen: Screen): void;
  toggleThreads(): void;
  setThreadsOpen(open: boolean): void;
  setTheme(theme: ThemePref): void;
  setMotion(motion: MotionPref): void;
  openTaskPanel(id: string): void;
  closeTaskPanel(): void;
}

export const useUi = create<UiState>()((set, get) => ({
  screen: "conversation",
  threadsOpen: false,
  theme: storedTheme(),
  motion: storedMotion(),
  taskPanelId: null,
  setScreen: (screen) => set({ screen, threadsOpen: false }),
  toggleThreads: () => set((s) => ({ threadsOpen: !s.threadsOpen })),
  setThreadsOpen: (threadsOpen) => set({ threadsOpen }),
  setTheme: (theme) => {
    write(THEME_KEY, theme);
    applyAppearance(theme, get().motion);
    set({ theme });
  },
  setMotion: (motion) => {
    write(MOTION_KEY, motion);
    applyAppearance(get().theme, motion);
    set({ motion });
  },
  openTaskPanel: (taskPanelId) => set({ taskPanelId }),
  closeTaskPanel: () => set({ taskPanelId: null }),
}));

/** Re-apply when the OS theme / motion preference changes and we follow it. */
export function watchSystemAppearance(): () => void {
  const queries = ["(prefers-color-scheme: dark)", "(prefers-reduced-motion: reduce)"].map((q) => window.matchMedia(q));
  const sync = () => applyAppearance(useUi.getState().theme, useUi.getState().motion);
  queries.forEach((q) => q.addEventListener("change", sync));
  return () => queries.forEach((q) => q.removeEventListener("change", sync));
}
