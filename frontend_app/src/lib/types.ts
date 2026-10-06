export type ProviderId = "groq" | "gemini" | "openrouter" | "custom" | "local";
export type MessageStatus = "complete" | "streaming" | "stopped" | "error";

export interface Conversation {
  id: string;
  title: string;
  persona_id: string;
  created_at: string;
  updated_at: string;
  web?: boolean | null; // this conversation's web switch; null or absent follows Settings
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  status: MessageStatus;
  meta: Record<string, unknown>;
  created_at: string;
}

export interface RouteEntry {
  provider: ProviderId;
  model: string;
  context_size?: number;
  reasoning?: boolean;
}

export interface LocalLLMSettings {
  model_path: string;
  context_size: number;
  threads: number;
  gpu_layers: number;
}

export interface AppSettings {
  roles: Record<string, RouteEntry[]>;
  custom_base_url: string;
  private_mode: boolean;
  internet: boolean;
  local_llm: LocalLLMSettings;
  temperature: number;
  max_tokens: number;
  agent_max_tokens: number;
  history_window: number;
  system1: System1Settings;
  memory: MemorySettings;
  context_caps: Record<string, number>;
  auto_approve_skills: boolean;
  replay_thumbnails: boolean;
  use_learned_skills: boolean;
  token_saving: { mask_superseded: boolean; mask_batch: number; compact_schemas: boolean; tool_groups: boolean };
}

export interface MemorySettings {
  facts_enabled: boolean;
  fact_threshold: number;
  episodic_enabled: boolean;
  episodic_min_score: number;
  facts_k: number;
  episodes_k: number;
}

export interface System1Settings {
  enabled: boolean;
  auto_tasks: boolean;
  intent_threshold: number;
  stop_threshold: number;
  skill_threshold: number;
  judge_threshold: number;
}

export interface ProviderInfo {
  id: ProviderId;
  label: string;
  needs_key: boolean;
  has_key: boolean;
  key_optional?: boolean;
  base_url: string;
}

export interface ProviderTestResult {
  ok: boolean;
  latency_ms: number;
  reply: string | null;
  error: string | null;
}

export type TaskStateName =
  | "planning" | "running" | "waiting_approval" | "paused" | "verifying" | "done" | "failed" | "cancelled";

export interface TaskRecord {
  id: string;
  conversation_id: string;
  goal: string;
  state: TaskStateName;
  plan: string[];
  plan_done: number[];
  checks: Record<string, unknown>[];
  summary: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  active_seconds?: number;
  knowledge?: string[];
  skill_id?: string | null;
}

export interface StepRecord {
  id: string;
  task_id: string;
  idx: number;
  tool: string;
  args: Record<string, unknown>;
  summary: string;
  verdict: "allow" | "ask" | "deny";
  ok: boolean | null;
  result: string | null;
  duration_ms: number | null;
  created_at: string;
  untrusted?: boolean;
  meta?: { app?: string | null; element?: { role: string; name: string; window: string } | null } | null;
  decider?: "agent" | "macro";
  thumbnail?: string | null;
}

export interface PendingApproval {
  approval_id: string;
  task_id: string;
  step_id: string;
  tool: string;
  summary: string;
  reason: string;
  tier: "read" | "write" | "irreversible";
}

export interface TaskDetail {
  task: TaskRecord;
  steps: StepRecord[];
  approvals: PendingApproval[];
  check_descriptions: string[];
}
