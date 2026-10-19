import { useQuery } from "@tanstack/react-query";
import { api, apiBlob } from "../../lib/api";
import type { Conversation } from "../../lib/types";
import { useSession } from "../../stores/session";
import { useUi } from "../../stores/ui";

export const SECTIONS = ["Personality", "Backstory", "Speaking style", "Boundaries"] as const;
export type ImageFrequency = "never" | "on_request" | "sometimes";

export interface Accent {
  light: string;
  dark: string;
  source: "anchor" | "manual";
}

export interface PersonaVoice {
  blend: Record<string, number>;
  speed: number;
}

export interface Persona {
  id: string;
  name: string;
  built_in: boolean;
  voice: PersonaVoice;
  accent: Accent | null;
  image_style: string;
  anchors: string[];
  sheet: string[];
  image_frequency: ImageFrequency;
  tone_engine: boolean;
  created: string | null;
  sections: Record<string, string>;
  style?: string | null; // the imported style directive
  lines?: number;        // imported messages kept as style examples
}

export const BUILTIN_ID = "aethel";

export function usePersonas() {
  return useQuery({ queryKey: ["personas"], queryFn: () => api<Persona[]>("/api/personas") });
}

/** Who the user is talking to: the open conversation's persona, or the one chosen for the next new conversation. */
export function useActivePersonaId(): string {
  const conversationId = useSession((s) => s.conversationId);
  const next = useUi((s) => s.newChatPersona);
  const { data: conversations } = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api<Conversation[]>("/api/conversations"),
    enabled: conversationId !== null,
  });
  if (conversationId === null) return next;
  const conv = Array.isArray(conversations) ? conversations.find((c) => c.id === conversationId) : undefined;
  return conv?.persona_id ?? next;
}

export function useActivePersona(): Persona | undefined {
  const id = useActivePersonaId();
  const { data: personas } = usePersonas();
  return Array.isArray(personas) ? personas.find((p) => p.id === id) : undefined;
}

/** A fresh conversation with this persona, shown straight away (it's created on the first message). */
export function talkTo(personaId: string): void {
  useSession.getState().setConversation(null, []);
  useUi.getState().setNewChatPersona(personaId);
  useUi.getState().setScreen("conversation");
}

/** Weights that add up to 1, voices with no weight dropped; at least the first voice survives. */
export function normaliseBlend(rows: { voice: string; weight: number }[]): Record<string, number> {
  const summed: Record<string, number> = {};
  for (const r of rows) if (r.voice && r.weight > 0) summed[r.voice] = (summed[r.voice] ?? 0) + r.weight;
  if (Object.keys(summed).length === 0 && rows[0]?.voice) summed[rows[0].voice] = 1;
  const total = Object.values(summed).reduce((a, b) => a + b, 0) || 1;
  return Object.fromEntries(Object.entries(summed).map(([v, w]) => [v, Math.round((w / total) * 1000) / 1000]));
}

/** Plays a voice blend through Web Audio (the app's CSP allows no blob: media). */
export async function previewVoice(blend: Record<string, number>, speed: number, text: string): Promise<void> {
  const blob = await apiBlob("/api/voice/preview", { method: "POST", body: JSON.stringify({ blend: { ...blend, speed }, text }) });
  const ctx = new AudioContext();
  const source = ctx.createBufferSource();
  source.buffer = await ctx.decodeAudioData(await blob.arrayBuffer());
  source.connect(ctx.destination);
  source.onended = () => void ctx.close();
  source.start();
}
