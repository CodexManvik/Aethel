import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../../lib/api";
import { Button } from "../../ui/Button";
import type { Persona } from "./personasApi";

const MAX_BYTES = 20_000_000;

interface ImportOut {
  speakers: { name: string; messages: number }[];
  speaker: string | null;
  messages: number;
  directive: string | null;
  import_id: string | null;
}

const why = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** A chat export → who to learn from → a style directive to edit → saved (with the lines as style examples). */
export function StyleImport({ persona }: { persona: Persona }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<{ name: string; content: string } | null>(null);
  const [found, setFound] = useState<ImportOut | null>(null);
  const [directive, setDirective] = useState("");
  const base = `/api/personas/${persona.id}/style`;
  const done = () => void qc.invalidateQueries({ queryKey: ["personas"] });
  const reset = () => { setFile(null); setFound(null); setDirective(""); };

  const read = useMutation({
    mutationFn: async (picked: File) => {
      if (picked.size > MAX_BYTES) throw new Error("That file is over 20 MB.");
      const content = await picked.text();
      setFile({ name: picked.name, content });
      return api<ImportOut>(`${base}/import`, { method: "POST", body: JSON.stringify({ filename: picked.name, content }) });
    },
    onSuccess: setFound,
    onError: (e) => toast.error(why(e)),
  });
  const pick = useMutation({
    mutationFn: (speaker: string) => api<ImportOut>(`${base}/import`, { method: "POST",
      body: JSON.stringify({ filename: file?.name ?? "", content: file?.content ?? "", speaker }) }),
    onSuccess: (r) => { setFound(r); setDirective(r.directive ?? ""); },
    onError: (e) => toast.error(why(e)),
  });
  const save = useMutation({
    mutationFn: () => api<Persona>(base, { method: "PUT", body: JSON.stringify({ directive, import_id: found?.import_id ?? null }) }),
    onSuccess: () => { toast(`Saved how ${persona.name} writes`); reset(); done(); },
    onError: (e) => toast.error(why(e)),
  });
  const clear = useMutation({
    mutationFn: () => api<void>(base, { method: "DELETE" }),
    onSuccess: done,
    onError: (e) => toast.error(why(e)),
  });

  if (found?.directive !== null && found?.directive !== undefined) {
    return (
      <div className="flex flex-col gap-3">
        <p className="text-[12.5px] text-muted">From {found.messages} messages by {found.speaker}. Edit it before saving.</p>
        <textarea aria-label="Style directive" value={directive} onChange={(e) => setDirective(e.target.value)} rows={4}
          className="w-full rounded-lg border border-hairline bg-paper p-3 text-[13.5px] leading-6 text-ink outline-none focus:border-muted" />
        <div className="flex gap-2">
          <Button variant="primary" disabled={save.isPending} onClick={() => save.mutate()}>Save style</Button>
          <Button onClick={reset}>Cancel</Button>
        </div>
      </div>
    );
  }
  if (found) {
    return (
      <div className="flex flex-col gap-2" role="radiogroup" aria-label="Whose messages">
        <p className="text-[12.5px] text-muted">Whose way of writing should {persona.name} learn?</p>
        {found.speakers.slice(0, 8).map((s) => (
          <button key={s.name} type="button" role="radio" aria-checked={false} disabled={pick.isPending}
            onClick={() => pick.mutate(s.name)}
            className="flex w-72 items-center justify-between rounded-lg border border-hairline px-3 py-2 text-left text-[13px] hover:border-muted">
            <span>{s.name}</span><span className="text-faint">{s.messages} messages</span>
          </button>
        ))}
        <Button className="w-fit" onClick={reset}>Cancel</Button>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      {persona.style ? (
        <div className="rounded-lg border border-hairline p-3">
          <p className="font-voice text-[14px] italic leading-6 text-ink">{persona.style}</p>
          <p className="mt-1 text-[12px] text-faint">{persona.lines ? `${persona.lines} messages kept as examples` : "No examples kept"}</p>
        </div>
      ) : (
        <p className="text-[12.5px] text-muted">WhatsApp (.txt), Telegram or Discord (.json) or a CSV. It's read on this PC; the messages stay here.</p>
      )}
      <div className="flex gap-2">
        <label className="inline-flex h-9 cursor-pointer items-center rounded-full border border-hairline bg-paper px-4 text-[13px] font-medium text-ink hover:border-muted">
          {read.isPending ? "Reading…" : "Import a chat export"}
          <input type="file" aria-label="Chat export" accept=".txt,.json,.csv" className="sr-only"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) read.mutate(f); e.target.value = ""; }} />
        </label>
        {persona.style && <Button variant="danger" disabled={clear.isPending} onClick={() => clear.mutate()}>Remove</Button>}
      </div>
    </div>
  );
}
