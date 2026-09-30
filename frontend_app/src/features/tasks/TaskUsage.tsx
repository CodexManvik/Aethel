import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { describeUsage, type UsageTotals } from "../../lib/usage";

/** The task's token use so far; re-read as the task moves (a new step, a new state). */
export function TaskUsage({ taskId, version }: { taskId: string; version: string }) {
  const [usage, setUsage] = useState<UsageTotals | null>(null);
  useEffect(() => {
    let live = true;
    api<UsageTotals>(`/api/tasks/${encodeURIComponent(taskId)}/usage`)
      .then((u) => { if (live) setUsage(u); })
      .catch(() => {});
    return () => { live = false; };
  }, [taskId, version]);
  if (!usage || usage.calls === 0) return null;
  return <p aria-label="Tokens used" className="text-[11.5px] text-faint">{describeUsage(usage)}</p>;
}
