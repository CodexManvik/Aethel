import type { ApprovalUi } from "../../stores/tasks";
import { Button } from "../../ui/Button";
import { decideApproval } from "./taskActions";

const VERBS: Record<string, string> = {
  fs_write: "write a file",
  fs_read: "read a file",
  fs_list: "look inside a folder",
  shell_run: "run a command",
  win_app: "open or switch apps",
  open_url: "open a web page",
  web_read: "read a web page",
  web_search: "search the web",
  browser_click: "click on a web page",
  browser_type: "type on a web page",
  win_click: "click",
  win_type: "type",
  win_shortcut: "press a shortcut",
  word_save: "save a Word document",
  excel_save: "save a workbook",
  ppt_save: "save a presentation",
};
const FAMILIES: [RegExp, string][] = [
  [/^win_/, "use an app on your screen"],
  [/^browser_/, "use its own browser"],
  [/^(word|excel|ppt)_/, "work in Office"],
];

export function approvalVerb(tool: string): string {
  return VERBS[tool] ?? FAMILIES.find(([re]) => re.test(tool))?.[1] ?? `use ${tool}`;
}

export function ApprovalCard({ approval }: { approval: ApprovalUi }) {
  const verb = approvalVerb(approval.tool);
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
