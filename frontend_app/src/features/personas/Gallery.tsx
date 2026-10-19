import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../../lib/api";
import { useObjectUrl } from "../../lib/useObjectUrl";
import { cn } from "../../ui/cn";
import type { Persona } from "./personasApi";

export interface GalleryItem {
  id: string;
  url: string;
  scene: string;
  created: string;
  score: number | null;
  low: boolean;
  reference: boolean;
  reason: "chat" | "task" | "studio";
}

export const MAX_REFERENCES = 6;
const day = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
};

function Tile({ item, onOpen }: { item: GalleryItem; onOpen(): void }) {
  const src = useObjectUrl(item.url);
  const dot = item.score === null ? "bg-faint" : item.low ? "bg-accent" : "bg-[#5b8a5a]";
  const label = item.score === null ? "face not checked" : item.low ? "face may differ" : "face matches";
  return (
    <li className="flex flex-col gap-1">
      <button type="button" onClick={onOpen} aria-label={item.scene}
        className="aspect-[3/4] overflow-hidden rounded-lg border border-hairline hover:border-muted">
        {src ? <img src={src} alt="" className="size-full object-cover" /> : <span className="grain block size-full bg-well" />}
      </button>
      <span className="flex items-center gap-1.5 text-[11px] text-faint">
        <span className={cn("size-1.5 rounded-full", dot)} title={label} aria-label={label} />
        {day(item.created)}
        {item.reference && <span className="ml-auto text-muted">reference</span>}
      </span>
    </li>
  );
}

/** The persona's "memories" album: the photos it has sent, with how well each face matched. */
export function Gallery({ persona }: { persona: Persona }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState<GalleryItem | null>(null);
  const { data } = useQuery({ queryKey: ["gallery", persona.id],
    queryFn: () => api<GalleryItem[]>(`/api/personas/${persona.id}/gallery`) });
  const items = (Array.isArray(data) ? data : []).filter((g) => g.reason !== "studio");
  const used = persona.anchors.length + persona.sheet.length + items.filter((g) => g.reference).length;
  const toggle = useMutation({
    mutationFn: (g: GalleryItem) => api<GalleryItem>(`/api/personas/${persona.id}/gallery/${g.id}/reference`,
      { method: "POST", body: JSON.stringify({ on: !g.reference }) }),
    onSuccess: (g) => { setOpen((o) => (o && o.id === g.id ? { ...o, reference: g.reference } : o));
      void qc.invalidateQueries({ queryKey: ["gallery", persona.id] }); },
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  });
  const big = useObjectUrl(open?.url ?? null);

  if (items.length === 0) return <p className="font-voice text-[14px] italic text-muted">No photos yet.</p>;
  return (
    <>
      <ul className="grid grid-cols-4 gap-3">
        {items.map((g) => <Tile key={g.id} item={g} onOpen={() => setOpen(g)} />)}
      </ul>
      <Dialog.Root open={open !== null} onOpenChange={(o) => !o && setOpen(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-ink/60" />
          <Dialog.Content aria-describedby={undefined}
            className="fixed top-1/2 left-1/2 z-50 flex max-h-[90vh] -translate-x-1/2 -translate-y-1/2 flex-col gap-3 rounded-xl bg-paper p-4">
            {open && (
              <>
                {big && <img src={big} alt="" className="max-h-[70vh] rounded-lg object-contain" />}
                <Dialog.Title className="max-w-md font-voice text-[14px] font-normal italic text-ink-2">{open.scene}</Dialog.Title>
                <div className="flex items-center gap-3 text-[12.5px]">
                  <label className="flex items-center gap-2 text-ink">
                    <input type="checkbox" checked={open.reference}
                      disabled={!open.reference && used >= MAX_REFERENCES}
                      onChange={() => toggle.mutate(open)} />
                    Use as reference
                  </label>
                  <span className="text-faint">{used}/{MAX_REFERENCES} references</span>
                  <Dialog.Close className="ml-auto text-muted hover:text-ink">Close</Dialog.Close>
                </div>
              </>
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
