import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Plus } from "lucide-react";
import { toast } from "sonner";
import { api } from "../../lib/api";
import { useUi } from "../../stores/ui";
import { Button } from "../../ui/Button";
import { cn } from "../../ui/cn";
import { PersonaAvatar } from "./PersonaAvatar";
import { PersonaEditor } from "./PersonaEditor";
import { usePersonas, type Persona } from "./personasApi";

const why = (e: unknown) => (e instanceof Error ? e.message : String(e));
const STEPS = ["Describe", "Draft", "Face", "Character sheet", "Voice"];

/** The persona studio: the list, a new persona from a description, and the editor. */
export function PersonasView() {
  const studioId = useUi((s) => s.studioPersonaId);
  const [creating, setCreating] = useState(false);
  const { data } = usePersonas();
  const personas = Array.isArray(data) ? data : [];
  const open = personas.find((p) => p.id === studioId);

  return (
    <div className="h-full overflow-y-auto bg-paper">
      <div className="mx-auto max-w-3xl px-10 py-10">
        {open ? (
          <PersonaEditor key={open.id} persona={open} onBack={() => useUi.getState().openStudio(null)} />
        ) : creating ? (
          <CreateFlow onCancel={() => setCreating(false)} onCreated={(p) => { setCreating(false); useUi.getState().openStudio(p.id); }} />
        ) : (
          <PersonaList personas={personas} onNew={() => setCreating(true)} />
        )}
      </div>
    </div>
  );
}

function PersonaList({ personas, onNew }: { personas: Persona[]; onNew(): void }) {
  return (
    <>
      <h1 className="font-display text-[44px] leading-none">Personas</h1>
      <p className="mt-2 font-voice text-[15px] text-muted">Who you talk to. Each one has its own voice, memories between you, and way of writing.</p>
      <ul className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-3">
        {personas.map((p) => (
          <li key={p.id}>
            <button type="button" onClick={() => useUi.getState().openStudio(p.id)} aria-label={`Edit ${p.name}`}
              className="flex w-full flex-col items-center gap-3 rounded-xl border border-hairline bg-paper px-4 py-6 hover:border-muted">
              <PersonaAvatar persona={p} size={64} />
              <span className="font-display text-[20px] leading-none">{p.name}</span>
              <span className="flex items-center gap-1.5 text-[11.5px] text-faint">
                {p.accent && <span className="size-2.5 rounded-full" style={{ background: p.accent.light }} />}
                {p.built_in ? "built in" : p.image_frequency === "never" ? "no photos" : "companion"}
              </span>
            </button>
          </li>
        ))}
        <li>
          <button type="button" onClick={onNew}
            className="flex size-full min-h-[150px] flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-hairline text-muted hover:border-muted hover:text-ink">
            <Plus size={20} strokeWidth={1.5} />
            <span className="text-[13px]">New persona</span>
          </button>
        </li>
      </ul>
    </>
  );
}

function Steps({ at }: { at: number }) {
  return (
    <ol className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-[11.5px] uppercase tracking-[0.12em]" aria-label="Steps">
      {STEPS.map((s, i) => (
        <li key={s} aria-current={i === at ? "step" : undefined}
          className={cn(i === at ? "text-accent" : "text-muted")}>
          {i + 1}. {s}
        </li>
      ))}
    </ol>
  );
}

function CreateFlow({ onCancel, onCreated }: { onCancel(): void; onCreated(p: Persona): void }) {
  const qc = useQueryClient();
  const [description, setDescription] = useState("");
  const create = async (draft: Pick<Persona, "name" | "sections" | "image_style">) => {
    const made = await api<Persona>("/api/personas", { method: "POST", body: JSON.stringify(draft) });
    await qc.invalidateQueries({ queryKey: ["personas"] });
    return made;
  };
  const draft = useMutation({
    mutationFn: async () => {
      const d = await api<Persona>("/api/personas/draft", { method: "POST", body: JSON.stringify({ description }) });
      return create({ name: d.name, sections: d.sections, image_style: d.image_style });
    },
    onSuccess: onCreated,
    onError: (e) => toast.error(why(e)),
  });
  const blank = useMutation({
    mutationFn: () => create({ name: "New persona", sections: {}, image_style: "" }),
    onSuccess: onCreated,
    onError: (e) => toast.error(why(e)),
  });
  return (
    <>
      <button type="button" onClick={onCancel} className="mb-6 flex items-center gap-1.5 text-[13px] text-muted hover:text-ink">
        <ArrowLeft size={14} /> Personas
      </button>
      <h1 className="font-display text-[44px] leading-none">A new persona</h1>
      <Steps at={draft.isPending ? 1 : 0} />
      <p className="mt-6 font-voice text-[15px] text-muted">
        Describe who they are: age, where they live, what they love, how they talk. I'll draft the rest for you to edit.
      </p>
      <textarea aria-label="Describe the persona" value={description} maxLength={2000} rows={6}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="A dry-humoured marine biologist in her late twenties who lives in Lisbon, loves film photography…"
        className="mt-3 w-full rounded-xl border border-hairline bg-paper p-4 text-[14px] leading-6 text-ink placeholder:text-faint outline-none focus:border-muted" />
      <div className="mt-3 flex items-center gap-3">
        <Button variant="primary" disabled={description.trim().length < 3 || draft.isPending} onClick={() => draft.mutate()}>
          {draft.isPending ? "Drafting…" : "Draft it"}
        </Button>
        <Button disabled={draft.isPending || blank.isPending} onClick={() => blank.mutate()}>Write it myself</Button>
        <span className="ml-auto text-[12px] text-faint">Then you pick a face, a character sheet and a voice.</span>
      </div>
    </>
  );
}
