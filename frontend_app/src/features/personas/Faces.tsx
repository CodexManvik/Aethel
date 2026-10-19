import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../../lib/api";
import { useObjectUrl } from "../../lib/useObjectUrl";
import { Button } from "../../ui/Button";
import { cn } from "../../ui/cn";
import { personaFile, usePortraitVersion, type Persona } from "./personasApi";

export interface ImagesStatus {
  backend: string | null;
  why_not: string;
  face_models: boolean;
  downloading: boolean;
  progress: number;
  error: string | null;
}

interface Made {
  id: string;
  ok: boolean;
  url?: string;
  score?: number | null;
  message?: string;
}

const why = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function useImagesStatus() {
  return useQuery({
    queryKey: ["images", "status"],
    queryFn: () => api<ImagesStatus>("/api/images/status"),
    refetchInterval: (q) => (q.state.data?.downloading ? 1000 : false),
  });
}

function Thumb({ path, label, onClick, selected }: { path: string; label: string; onClick?(): void; selected?: boolean }) {
  const src = useObjectUrl(path);
  const img = src ? <img src={src} alt={label} className="size-full object-cover" /> : <span className="grain block size-full bg-well" />;
  return onClick ? (
    <button type="button" aria-label={label} onClick={onClick}
      className={cn("aspect-[3/4] overflow-hidden rounded-lg border", selected ? "border-accent" : "border-hairline hover:border-muted")}>
      {img}
    </button>
  ) : <div className="aspect-[3/4] overflow-hidden rounded-lg border border-hairline" title={label}>{img}</div>;
}

/** The face check's download (asked for here, never automatic: it's about 250 MB). */
function FaceCheck({ status }: { status: ImagesStatus }) {
  const qc = useQueryClient();
  const start = useMutation({
    mutationFn: () => api("/api/images/face-models", { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["images", "status"] }),
    onError: (e) => toast.error(why(e)),
  });
  if (status.face_models) return <p className="text-[12px] text-faint">The face check is installed: every photo is compared with the chosen face.</p>;
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-hairline p-3">
      <p className="text-[12.5px] text-muted">
        Without the face check, photos aren't compared with the chosen face. It's two small open models (about 250 MB) that run on this PC.
      </p>
      {status.downloading ? (
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-well" role="progressbar" aria-label="Downloading the face check"
          aria-valuenow={Math.round(status.progress * 100)}>
          <div className="h-full bg-accent transition-[width]" style={{ width: `${status.progress * 100}%` }} />
        </div>
      ) : (
        <Button className="w-fit" onClick={() => start.mutate()}>Download the face check</Button>
      )}
      {status.error && <p role="alert" className="text-[12px] text-accent">{status.error}</p>}
    </div>
  );
}

/** Studio steps 3–4: four faces from the image style, pick one, then the character sheet from it. */
export function Faces({ persona }: { persona: Persona }) {
  const qc = useQueryClient();
  const { data: status } = useImagesStatus();
  const [faces, setFaces] = useState<Made[]>([]);
  const version = usePortraitVersion((s) => s.n);
  const file = (rel: string) => personaFile(persona.id, rel, version);
  const refresh = () => {
    usePortraitVersion.getState().bump(); // same file names, new pictures
    void qc.invalidateQueries({ queryKey: ["personas"] });
    void qc.invalidateQueries({ queryKey: ["gallery", persona.id] });
  };
  const generate = useMutation({
    mutationFn: () => api<Made[]>(`/api/personas/${persona.id}/portraits`, { method: "POST" }),
    onSuccess: (made) => {
      setFaces(made.filter((m) => m.ok));
      if (!made.some((m) => m.ok)) toast.error(made[0]?.message ?? "No faces came out.");
    },
    onError: (e) => toast.error(why(e)),
  });
  const pick = useMutation({
    mutationFn: (id: string) => api<Persona>(`/api/personas/${persona.id}/anchor`, { method: "POST", body: JSON.stringify({ image_id: id }) }),
    onSuccess: () => { setFaces([]); refresh(); },
    onError: (e) => toast.error(why(e)),
  });
  const sheet = useMutation({
    mutationFn: () => api<{ results: Made[] }>(`/api/personas/${persona.id}/sheet`, { method: "POST" }),
    onSuccess: (r) => {
      refresh();
      const failed = r.results.filter((m) => !m.ok).length;
      if (failed) toast.error(`${failed} of the views didn't come out; try again later.`);
    },
    onError: (e) => toast.error(why(e)),
  });
  if (!status) return null;
  const noBackend = status.backend === null;

  return (
    <div className="flex flex-col gap-4">
      {noBackend && <p className="text-[12.5px] text-accent">{status.why_not}</p>}
      {faces.length > 0 ? (
        <>
          <p className="text-[12.5px] text-muted">Pick the face that is {persona.name}.</p>
          <div className="grid w-80 grid-cols-2 gap-3">
            {faces.map((f, i) => (
              <Thumb key={f.id} path={f.url!} label={`Face ${i + 1}`} onClick={() => pick.mutate(f.id)} />
            ))}
          </div>
        </>
      ) : persona.anchors.length > 0 ? (
        <div className="flex items-start gap-3">
          <div className="w-28"><Thumb path={file(persona.anchors[0])} label="The face" /></div>
          {persona.sheet.map((s) => <div key={s} className="w-20"><Thumb path={file(s)} label={s.split("/").pop()!} /></div>)}
        </div>
      ) : (
        <p className="font-voice text-[14px] italic text-muted">No face yet.</p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button disabled={noBackend || generate.isPending} onClick={() => generate.mutate()}>
          {generate.isPending ? "Drawing four faces…" : persona.anchors.length ? "New faces" : "Generate 4 faces"}
        </Button>
        {persona.anchors.length > 0 && faces.length === 0 && (
          <Button disabled={noBackend || sheet.isPending} onClick={() => sheet.mutate()}>
            {sheet.isPending ? "Drawing the sheet…" : persona.sheet.length ? "Redo the character sheet" : "Make the character sheet"}
          </Button>
        )}
      </div>
      <FaceCheck status={status} />
    </div>
  );
}
