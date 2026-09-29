import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, apiBlob } from "../../lib/api";
import type { StepRecord, TaskDetail, TaskRecord } from "../../lib/types";
import { Button } from "../../ui/Button";
import { cn } from "../../ui/cn";

function useObjectUrl(path: string | null): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return setUrl(null);
    let alive = true;
    let made: string | null = null;
    apiBlob(path).then((blob) => {
      if (!alive) return;
      made = URL.createObjectURL(blob);
      setUrl(made);
    }).catch(() => alive && setUrl(null));
    return () => {
      alive = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [path]);
  return url;
}

async function exportTask(task: TaskRecord) {
  try {
    const blob = await apiBlob(`/api/tasks/${task.id}/export`);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `aethel-${task.id}.zip`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  } catch (e) {
    toast(`Couldn't export this task: ${String(e)}`);
  }
}

function Decider({ step }: { step: StepRecord }) {
  const macro = step.decider === "macro";
  return (
    <span className={cn("rounded-full border px-1.5 py-px text-[10.5px]", macro ? "border-accent text-accent" : "border-hairline text-muted")}>
      {macro ? "replayed" : "thought through"}
    </span>
  );
}

/** A task's steps as a scrubbable timeline: arrow keys move through them. */
export function Timeline({ task }: { task: TaskRecord }) {
  const { data } = useQuery({ queryKey: ["tasks", "detail", task.id], queryFn: () => api<TaskDetail>(`/api/tasks/${task.id}`) });
  const steps = data?.steps ?? [];
  const [index, setIndex] = useState(0);
  const listRef = useRef<HTMLOListElement>(null);
  useEffect(() => setIndex(0), [task.id]);
  const step = steps[Math.min(index, Math.max(steps.length - 1, 0))];
  const thumb = useObjectUrl(step?.thumbnail ? `/api/tasks/${task.id}/steps/${step.id}/thumbnail` : null);

  const onKey = (e: KeyboardEvent) => {
    const next = { ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: steps.length - 1 }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    const clamped = Math.max(0, Math.min(steps.length - 1, next));
    setIndex(clamped);
    (listRef.current?.children[clamped] as HTMLElement | undefined)?.querySelector("button")?.focus();
  };

  return (
    <div>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-display text-[28px] leading-tight text-ink">{task.goal}</h2>
          {task.summary && <p className="mt-1 font-voice text-[15px] text-ink-2">{task.summary}</p>}
          {task.error && <p className="mt-1 text-[13px] text-accent">{task.error}</p>}
        </div>
        <Button onClick={() => void exportTask(task)}>Export</Button>
      </div>
      {steps.length === 0 ? (
        <p className="mt-6 font-voice text-[15px] text-muted">No steps were taken.</p>
      ) : (
        <div className="mt-6 flex gap-6">
          <ol ref={listRef} aria-label="Steps" onKeyDown={onKey} className="w-72 shrink-0 space-y-1">
            {steps.map((s, i) => (
              <li key={s.id}>
                <button
                  type="button"
                  aria-current={i === index ? "step" : undefined}
                  onClick={() => setIndex(i)}
                  className={cn("flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-[12.5px]", i === index ? "bg-canvas text-ink" : "text-ink-2 hover:bg-canvas/60")}
                >
                  <span className={cn("mt-1 size-2 shrink-0 rounded-full", s.ok ? "bg-ink" : s.ok === false ? "bg-accent" : "bg-hairline")} aria-hidden="true" />
                  <span className="min-w-0 flex-1 truncate">{s.summary || s.tool}</span>
                  <span className="shrink-0 text-muted">{s.duration_ms != null ? `${(s.duration_ms / 1000).toFixed(1)} s` : ""}</span>
                </button>
              </li>
            ))}
          </ol>
          {step && (
            <figure className="min-w-0 flex-1">
              {thumb ? (
                <img src={thumb} alt={`The screen after: ${step.summary || step.tool}`} className="w-full max-w-[480px] rounded-lg border border-hairline" />
              ) : (
                <div className="grid aspect-video w-full max-w-[480px] place-items-center rounded-lg border border-dashed border-hairline text-[12.5px] text-muted">
                  No picture for this step
                </div>
              )}
              <figcaption className="mt-3 space-y-1 text-[12.5px] text-ink-2">
                <p className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-ink">{step.tool}</span>
                  <Decider step={step} />
                  <span className="text-muted">{step.ok ? "worked" : step.ok === false ? "failed" : "unfinished"}</span>
                </p>
                <p>{step.summary}</p>
                {step.meta?.element && (
                  <p className="text-muted">
                    {step.meta.element.role} “{step.meta.element.name}” in {step.meta.element.window}
                  </p>
                )}
              </figcaption>
            </figure>
          )}
        </div>
      )}
    </div>
  );
}
