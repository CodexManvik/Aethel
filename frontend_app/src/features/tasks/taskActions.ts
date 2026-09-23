import { api } from "../../lib/api";
import { getSocket } from "../../lib/session";

export function controlTask(taskId: string, action: "pause" | "resume" | "cancel") {
  getSocket().send({ type: "task_control", task_id: taskId, action });
}

export function decideApproval(approvalId: string, decision: "allow_once" | "allow_task" | "deny") {
  getSocket().send({ type: "approval_decision", approval_id: approvalId, decision });
}

/** Undo every file the task wrote. Returns how many changes were restored. */
export async function rollbackTask(taskId: string): Promise<number> {
  const out = await api<{ results: { ok: boolean; message: string }[] }>(`/api/tasks/${taskId}/rollback`, { method: "POST" });
  return out.results.filter((r) => r.ok).length;
}
