import { api } from "../../lib/api";
import { inTauri } from "../../lib/backend";
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

/** The kill switch: cancel every task, stopping any typing or clicking in progress. */
export function stopEverything() {
  getSocket().send({ type: "kill_switch" });
}

/** The desktop shell owns the global Ctrl+Alt+Esc hotkey and emits "kill-switch". */
export async function listenForKillSwitch(): Promise<() => void> {
  if (!inTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen("kill-switch", () => stopEverything());
}
