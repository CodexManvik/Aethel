import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { TaskRecord } from "../../lib/types";
import { cn } from "../../ui/cn";
import { Timeline } from "./Timeline";

const STATE_LABEL: Record<string, string> = {
  done: "done", failed: "couldn't finish", cancelled: "stopped", paused: "paused", running: "working",
  planning: "planning", waiting_approval: "needs you", verifying: "checking",
};

export function formatDuration(seconds: number | undefined): string {
  if (!seconds) return "";
  return seconds < 60 ? `${Math.round(seconds)} s` : `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
}

/** The Tasks screen (spec §4.5): every task, newest first, with a replay of its steps. */
export function TasksView() {
  const { data: tasks = [] } = useQuery({ queryKey: ["tasks", "recent"], queryFn: () => api<TaskRecord[]>("/api/tasks/recent") });
  const [selected, setSelected] = useState<string | null>(null);
  const current = tasks.find((t) => t.id === selected) ?? tasks[0];

  return (
    <div className="flex h-full bg-paper">
      <aside aria-label="Tasks" className="w-80 shrink-0 overflow-y-auto border-r border-hairline px-4 py-8">
        <h1 className="px-2 font-display text-[36px] leading-none">Tasks</h1>
        {tasks.length === 0 && <p className="mt-4 px-2 font-voice text-[15px] text-muted">No tasks yet.</p>}
        <ul className="mt-5 space-y-1">
          {tasks.map((t) => (
            <li key={t.id}>
              <button
                type="button"
                aria-current={t.id === current?.id}
                onClick={() => setSelected(t.id)}
                className={cn("w-full rounded-lg px-3 py-2 text-left", t.id === current?.id ? "bg-canvas" : "hover:bg-canvas/60")}
              >
                <span className="line-clamp-2 text-[13.5px] text-ink">{t.goal}</span>
                <span className="mt-0.5 block text-[11.5px] text-muted">
                  {STATE_LABEL[t.state] ?? t.state}
                  {t.active_seconds ? ` · ${formatDuration(t.active_seconds)}` : ""}
                  {` · ${new Date(t.created_at).toLocaleString()}`}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <section className="min-w-0 flex-1 overflow-y-auto px-8 py-8">{current && <Timeline task={current} />}</section>
    </div>
  );
}
