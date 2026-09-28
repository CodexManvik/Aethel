import { create } from "zustand";
import type { ServerEvent } from "../lib/events";
import type { TaskDetail, TaskStateName } from "../lib/types";

export const TERMINAL_STATES: TaskStateName[] = ["done", "failed", "cancelled"];

export interface StepUi {
  id: string;
  tool: string;
  summary: string;
  verdict: "allow" | "ask" | "deny";
  ok: boolean | null;
  detail: string;
  durationMs: number | null;
}

export interface ApprovalUi {
  id: string;
  stepId: string;
  tool: string;
  summary: string;
  reason: string;
  tier: "read" | "write" | "irreversible";
}

export interface CheckUi {
  description: string;
  passed: boolean | null;
  detail: string;
}

export interface TaskUi {
  id: string;
  conversationId: string;
  goal: string;
  state: TaskStateName;
  plan: string[];
  planDone: number[];
  checks: CheckUi[];
  steps: StepUi[];
  approvals: ApprovalUi[];
  summary: string | null;
  error: string | null;
}

export type TaskMap = Record<string, TaskUi>;

function patch(tasks: TaskMap, id: string, fn: (t: TaskUi) => TaskUi): TaskMap {
  const t = tasks[id];
  return t ? { ...tasks, [id]: fn(t) } : tasks;
}

export function applyTaskEvent(tasks: TaskMap, ev: ServerEvent): TaskMap {
  switch (ev.type) {
    case "task_created":
      return {
        ...tasks,
        [ev.task_id]: {
          id: ev.task_id, conversationId: ev.conversation_id, goal: ev.goal, state: "planning", plan: [],
          planDone: [], checks: [], steps: [], approvals: [], summary: null, error: null,
        },
      };
    case "task_plan":
      return patch(tasks, ev.task_id, (t) => ({
        ...t, plan: ev.steps, checks: ev.checks.map((d) => ({ description: d, passed: null, detail: "" })),
      }));
    case "plan_progress":
      return patch(tasks, ev.task_id, (t) =>
        t.planDone.includes(ev.index) ? t : { ...t, planDone: [...t.planDone, ev.index].sort((a, b) => a - b) });
    case "step_started":
      return patch(tasks, ev.task_id, (t) => ({
        ...t,
        steps: [...t.steps, { id: ev.step_id, tool: ev.tool, summary: ev.summary, verdict: ev.verdict, ok: null,
          detail: "", durationMs: null }],
      }));
    case "step_finished":
      return patch(tasks, ev.task_id, (t) => ({
        ...t,
        steps: t.steps.map((s) => (s.id === ev.step_id ? { ...s, ok: ev.ok, detail: ev.detail, durationMs: ev.duration_ms } : s)),
      }));
    case "approval_needed":
      return patch(tasks, ev.task_id, (t) => ({
        ...t,
        approvals: [...t.approvals, { id: ev.approval_id, stepId: ev.step_id, tool: ev.tool, summary: ev.summary,
          reason: ev.reason, tier: ev.tier }],
      }));
    case "approval_resolved":
      return patch(tasks, ev.task_id, (t) => ({ ...t, approvals: t.approvals.filter((a) => a.id !== ev.approval_id) }));
    case "verification":
      return patch(tasks, ev.task_id, (t) => ({
        ...t, checks: ev.results.map((r) => ({ description: r.description, passed: r.passed, detail: r.detail })),
      }));
    case "task_state":
      return patch(tasks, ev.task_id, (t) => ({
        ...t,
        state: ev.state,
        summary: ev.summary ?? t.summary,
        error: ev.error ?? t.error,
        approvals: TERMINAL_STATES.includes(ev.state) ? [] : t.approvals,
      }));
    default:
      return tasks;
  }
}

export function fromDetail(d: TaskDetail): TaskUi {
  return {
    id: d.task.id,
    conversationId: d.task.conversation_id,
    goal: d.task.goal,
    state: d.task.state,
    plan: d.task.plan,
    planDone: d.task.plan_done,
    checks: d.check_descriptions.map((description) => ({ description, passed: null, detail: "" })),
    steps: d.steps.map((s) => ({
      id: s.id, tool: s.tool, summary: s.summary, verdict: s.verdict, ok: s.ok,
      detail: (s.result ?? "").split("\n")[0], durationMs: s.duration_ms,
    })),
    approvals: d.approvals.map((a) => ({ id: a.approval_id, stepId: a.step_id, tool: a.tool, summary: a.summary,
      reason: a.reason, tier: a.tier })),
    summary: d.task.summary,
    error: d.task.error,
  };
}

interface TasksState {
  tasks: TaskMap;
  apply(ev: ServerEvent): void;
  hydrate(conversationId: string, details: TaskDetail[]): void;
}

export const useTasks = create<TasksState>()((set) => ({
  tasks: {},
  apply: (ev) => set((s) => ({ tasks: applyTaskEvent(s.tasks, ev) })),
  hydrate: (conversationId, details) =>
    set((s) => {
      const kept = Object.fromEntries(Object.entries(s.tasks).filter(([, t]) => t.conversationId !== conversationId));
      for (const d of details) kept[d.task.id] = fromDetail(d);
      return { tasks: kept };
    }),
}));
