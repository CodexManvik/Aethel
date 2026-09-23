import type { ApprovalUi } from "../../stores/tasks";
import { Button } from "../../ui/Button";
import { decideApproval } from "./taskActions";

const VERBS: Record<string, string> = {
  fs_write: "write a file",
  fs_read: "read a file",
  fs_list: "look inside a folder",
  shell_run: "run a command",
};

export function ApprovalCard({ approval }: { approval: ApprovalUi }) {
  const verb = VERBS[approval.tool] ?? `use ${approval.tool}`;
  return (
    <div role="group" aria-label="Approval needed" className="rounded-xl border border-accent/40 bg-accent-soft p-4">
      <p className="font-display text-[19px] leading-tight text-ink">Aethel wants to {verb}</p>
      <p className="mt-2 break-all rounded-md bg-paper px-2 py-1 font-mono text-[12px] text-ink">{approval.summary}</p>
      <p className="mt-2 text-[12.5px] leading-5 text-muted">{approval.reason}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="primary" onClick={() => decideApproval(approval.id, "allow_once")}>Allow once</Button>
        {approval.tier !== "irreversible" && (
          <Button onClick={() => decideApproval(approval.id, "allow_task")}>Allow for this task</Button>
        )}
        <Button variant="danger" onClick={() => decideApproval(approval.id, "deny")}>Deny</Button>
      </div>
    </div>
  );
}
