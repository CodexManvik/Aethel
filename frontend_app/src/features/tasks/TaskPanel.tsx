import { AnimatePresence, motion } from "motion/react";
import { Check, Pause, Play, Undo2, X } from "lucide-react";
import { toast } from "sonner";
import { TERMINAL_STATES, useTasks } from "../../stores/tasks";
import { useUi } from "../../stores/ui";
import { Button } from "../../ui/Button";
import { IconButton } from "../../ui/IconButton";
import { cn } from "../../ui/cn";
import { ApprovalCard } from "./ApprovalCard";
import { PenCheck } from "./PenCheck";
import { controlTask, rollbackTask } from "./taskActions";
import { inTauri } from "../../lib/backend";

const FILE_WRITES = /^fs_write$|_save$/; // steps "Undo file changes" can restore
const STATE_LABEL: Record<string, string> = {
  planning: "planning", running: "working", waiting_approval: "needs you", paused: "paused",
  verifying: "checking", done: "done", failed: "couldn't finish", cancelled: "stopped",
};

export function TaskPanel() {
  const taskId = useUi((s) => s.taskPanelId);
  const close = useUi((s) => s.closeTaskPanel);
  const task = useTasks((s) => (taskId ? s.tasks[taskId] : undefined));

  return (
    <AnimatePresence mode="wait">
      {task && (
        <motion.aside
          key={task.id}
          aria-label="Task"
          initial={{ opacity: 0, x: 24 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: 24 }}
          transition={{ type: "spring", stiffness: 260, damping: 30 }}
          className="flex w-[320px] shrink-0 flex-col gap-5 overflow-y-auto border-l border-hairline bg-canvas px-5 py-6"
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="text-[10.5px] uppercase tracking-[0.14em] text-faint">Task · {STATE_LABEL[task.state]}</p>
              <h2 className="mt-1.5 font-display text-[22px] leading-tight text-ink">{task.goal}</h2>
            </div>
            <IconButton label="Close task panel" onClick={close}><X size={15} /></IconButton>
          </div>

          {task.approvals.map((a) => <ApprovalCard key={a.id} approval={a} />)}

          {task.plan.length > 0 && (
            <ol aria-label="Plan" className="flex flex-col gap-2.5">
              {task.plan.map((step, i) => {
                const done = task.planDone.includes(i);
                const active = !done && !TERMINAL_STATES.includes(task.state) &&
                  i === task.plan.findIndex((_, j) => !task.planDone.includes(j));
                return (
                  <li key={i} data-done={done} className="flex gap-2.5 text-[13px] leading-5">
                    <PenCheck done={done} active={active} />
                    <span className={cn(done ? "text-ink" : "text-muted")}>{step}</span>
                  </li>
                );
              })}
            </ol>
          )}

          {task.steps.length > 0 && (
            <section>
              <p className="mb-2 text-[10.5px] uppercase tracking-[0.14em] text-faint">Activity</p>
              <ul className="flex flex-col gap-1.5">
                {task.steps.map((s) => (
                  <li key={s.id} className="flex items-baseline gap-2 text-[12px]">
                    <span className={cn("size-1.5 shrink-0 rounded-full",
                      s.ok === null ? "breathe bg-faint" : s.ok ? "bg-ink" : "bg-accent")} />
                    <span className="shrink-0 font-mono text-muted">{s.tool}</span>
                    <span className="min-w-0 truncate text-ink" title={s.summary}>{s.summary}</span>
                    {s.durationMs !== null && <span className="ml-auto shrink-0 text-faint">{s.durationMs} ms</span>}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {task.checks.length > 0 && (
            <section>
              <p className="mb-2 text-[10.5px] uppercase tracking-[0.14em] text-faint">Checks</p>
              <ul className="flex flex-col gap-1.5">
                {task.checks.map((c) => (
                  <li key={c.description} data-passed={c.passed === null ? "pending" : String(c.passed)}
                    className="flex items-baseline gap-2 text-[12px]">
                    <span className={cn("shrink-0", c.passed ? "text-ink" : c.passed === false ? "text-accent" : "text-faint")}>
                      {c.passed ? <Check size={12} /> : c.passed === false ? <X size={12} /> : "·"}
                    </span>
                    <span className="text-muted">{c.description}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {(task.summary || task.error) && (
            <div className="font-voice text-[15px] leading-6 text-ink-2">
              {task.summary && <p>{task.summary}</p>}
              {task.error && <p className="mt-1 font-sans text-[12.5px] text-accent">{task.error}</p>}
            </div>
          )}

          <div className="mt-auto flex flex-wrap gap-2 pt-2">
            {!TERMINAL_STATES.includes(task.state) && (
              <>
                {task.state === "paused" ? (
                  <Button onClick={() => controlTask(task.id, "resume")}><Play size={13} /> Resume</Button>
                ) : (
                  <Button onClick={() => controlTask(task.id, "pause")}><Pause size={13} /> Pause</Button>
                )}
                <Button variant="danger" aria-label="Cancel task" onClick={() => controlTask(task.id, "cancel")}>Cancel</Button>
              </>
            )}
            {TERMINAL_STATES.includes(task.state) && task.steps.some((s) => FILE_WRITES.test(s.tool) && s.ok) && (
              <Button
                onClick={async () => {
                  const n = await rollbackTask(task.id);
                  toast(n ? `Undid ${n} file change${n === 1 ? "" : "s"}.` : "Nothing to undo.");
                }}
              >
                <Undo2 size={13} /> Undo file changes
              </Button>
            )}
          </div>
          {!TERMINAL_STATES.includes(task.state) && inTauri() && (
            <p className="text-[11.5px] text-muted">Ctrl+Alt+Esc stops everything, from any app.</p>
          )}
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
