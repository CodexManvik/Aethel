import { create } from "zustand";

export interface ToneState {
  mood: number;   // -1 unpleasant … 1 pleasant
  energy: number; // -1 low … 1 high
  label: string;
}

interface ToneStore {
  byConversation: Record<string, ToneState>;
  set(conversationId: string, tone: ToneState): void;
}

/** A companion conversation's running tone (tone_changed events), for the accent glow. */
export const useTone = create<ToneStore>()((set) => ({
  byConversation: {},
  set: (conversationId, tone) => set((s) => ({ byConversation: { ...s.byConversation, [conversationId]: tone } })),
}));

/** How strongly the glow shows: calm conversations barely glow, lively ones more (0–0.55). */
export const glowOpacity = (tone: ToneState | undefined): number =>
  tone ? Math.round(((tone.energy + 1) / 2) * 0.55 * 100) / 100 : 0;
