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

export function useSaveNote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ app, body }: { app: string; body: string }) =>
      api<AppNote>(`/api/memory/notes/${encodeURIComponent(app)}`, { method: "PUT", body: JSON.stringify({ body }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["memory", "notes"] }),
  });
}
