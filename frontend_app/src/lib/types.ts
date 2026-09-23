export type ProviderId = "groq" | "gemini" | "openrouter" | "custom" | "local";
export type MessageStatus = "complete" | "streaming" | "stopped" | "error";

export interface Conversation {
  id: string;
  title: string;
  persona_id: string;
  created_at: string;
  updated_at: string;
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
}

export interface ProviderInfo {
  id: ProviderId;
  label: string;
  needs_key: boolean;
  has_key: boolean;
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
