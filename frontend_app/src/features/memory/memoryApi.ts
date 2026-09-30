import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";

export type SkillStatus = "quarantined" | "approved" | "deprecated";

export interface Skill {
  id: string;
  title: string;
  intent: string;
  apps: string[];
  status: SkillStatus;
  runs: number;
  successes: number;
  avg_duration_s: number | null;
  duration_history: number[];
  last_used: string | null;
  macro: string;
  steps: string[];
  pitfalls: string[];
}

export interface AppNote {
  app: string;
  facts: string[];
  body: string;
  updated: string | null;
}

export const useSkills = () => useQuery({ queryKey: ["memory", "skills"], queryFn: () => api<Skill[]>("/api/memory/skills") });
export const useNotes = () => useQuery({ queryKey: ["memory", "notes"], queryFn: () => api<AppNote[]>("/api/memory/notes") });

export function useSetSkillStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: SkillStatus }) =>
      api<Skill>(`/api/memory/skills/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ status }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["memory", "skills"] }),
  });
}

// ---- facts (Phase 3) -------------------------------------------------------------------
export interface Fact {
  id: string;
  scope: string;
  text: string;
  source_message_id: string | null;
  conversation_id: string | null;
  conversation_title: string | null;
  added_by: "extractor" | "user";
  created_at: string;
  updated_at: string;
}

export interface FactEvent {
  id: number;
  fact_id: string;
  op: "add" | "update" | "delete";
  old_text: string | null;
  new_text: string | null;
  actor: "extractor" | "user";
  source_message_id: string | null;
  created_at: string;
}

export interface EpisodicStatus {
  indexed: number;
  exchanges: number;
  rebuilding: boolean;
}

const FACTS = ["memory", "facts"] as const;

export const useFacts = (q: string) =>
  useQuery({
    queryKey: [...FACTS, q],
    queryFn: () => api<Fact[]>(`/api/memory/facts${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  });

export const useFactHistory = (id: string, enabled: boolean) =>
  useQuery({ queryKey: [...FACTS, "history", id], enabled, queryFn: () => api<FactEvent[]>(`/api/memory/facts/${encodeURIComponent(id)}/history`) });

function useFactMutation<V>(fn: (v: V) => Promise<unknown>) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => qc.invalidateQueries({ queryKey: FACTS }) });
}

export const useAddFact = () =>
  useFactMutation(({ scope, text }: { scope: string; text: string }) =>
    api<Fact>("/api/memory/facts", { method: "POST", body: JSON.stringify({ scope, text }) }));

export const useEditFact = () =>
  useFactMutation(({ id, text }: { id: string; text: string }) =>
    api<Fact>(`/api/memory/facts/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ text }) }));

export const useDeleteFact = () =>
  useFactMutation((id: string) => api<void>(`/api/memory/facts/${encodeURIComponent(id)}`, { method: "DELETE" }));

export const useEpisodicStatus = (poll: boolean) =>
  useQuery({ queryKey: ["memory", "episodic"], queryFn: () => api<EpisodicStatus>("/api/memory/episodic"), refetchInterval: poll ? 2000 : false });

export function useRebuildEpisodic() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ started: boolean }>("/api/memory/episodic/rebuild", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["memory", "episodic"] }),
  });
}

export function useSaveNote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ app, body }: { app: string; body: string }) =>
      api<AppNote>(`/api/memory/notes/${encodeURIComponent(app)}`, { method: "PUT", body: JSON.stringify({ body }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["memory", "notes"] }),
  });
}
