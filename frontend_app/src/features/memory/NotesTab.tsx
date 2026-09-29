import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "../../ui/Button";
import { inputClass } from "../../ui/Field";
import { cn } from "../../ui/cn";
import { useNotes, useSaveNote } from "./memoryApi";

export function NotesTab() {
  const { data: notes = [] } = useNotes();
  const [app, setApp] = useState<string | null>(null);
  const current = notes.find((n) => n.app === app) ?? notes[0];
  const [draft, setDraft] = useState("");
  const save = useSaveNote();
  useEffect(() => setDraft(current?.body ?? ""), [current?.app, current?.body]);

  if (notes.length === 0) {
    return <p className="py-6 font-voice text-[15px] text-muted">No app notes yet. Aethel writes them after tasks, as it learns how each app behaves.</p>;
  }
  return (
    <div className="flex gap-6 py-4">
      <ul aria-label="Apps" className="w-44 shrink-0 space-y-1">
        {notes.map((n) => (
          <li key={n.app}>
            <button
              type="button"
              aria-current={n.app === current?.app}
              onClick={() => setApp(n.app)}
              className={cn("w-full rounded-md px-2 py-1 text-left text-[13px] capitalize", n.app === current?.app ? "bg-canvas text-ink" : "text-muted hover:text-ink")}
            >
              {n.app} <span className="text-muted">· {n.facts.length}</span>
            </button>
          </li>
        ))}
      </ul>
      {current && (
        <div className="min-w-0 flex-1">
          <label htmlFor="note-body" className="text-[12.5px] text-muted">
            Notes on <span className="capitalize">{current.app}</span>, one fact per line starting with “- ”
          </label>
          <textarea
            id="note-body"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={14}
            className={cn(inputClass, "mt-2 w-full font-mono text-[12.5px]")}
          />
          <div className="mt-3">
            <Button
              variant="primary"
              disabled={draft === current.body}
              onClick={() => save.mutate({ app: current.app, body: draft }, { onSuccess: () => toast("Note saved.") })}
            >
              Save
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
