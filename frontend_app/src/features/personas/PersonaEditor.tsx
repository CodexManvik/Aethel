import { useState, type ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, MessageCircle } from "lucide-react";
import { toast } from "sonner";
import { api } from "../../lib/api";
import { Button } from "../../ui/Button";
import { cn } from "../../ui/cn";
import { inputClass } from "../../ui/Field";
import { Switch } from "../../ui/Switch";
import { Faces } from "./Faces";
import { Gallery } from "./Gallery";
import { PersonaAvatar } from "./PersonaAvatar";
import { SECTIONS, talkTo, type ImageFrequency, type Persona, type PersonaVoice } from "./personasApi";
import { StyleImport } from "./StyleImport";
import { useAutosave } from "./useAutosave";
import { VoiceBlendEditor } from "./VoiceBlendEditor";

type Patch = Partial<Pick<Persona, "name" | "sections" | "image_style" | "image_frequency" | "tone_engine">> & {
  voice?: PersonaVoice;
  accent_pick?: string | null;
};

const FREQUENCY: { id: ImageFrequency; label: string }[] = [
  { id: "never", label: "Never" },
  { id: "on_request", label: "When asked" },
  { id: "sometimes", label: "Sometimes" },
];
const HINTS: Record<string, string> = {
  Personality: "Who they are, in the second person: “You are…”.",
  Backstory: "Their history: things they can talk about. Only the first 600 characters reach each reply.",
  "Speaking style": "How they write: length, punctuation, slang, emoji.",
  Boundaries: "What they won't do, and how they say so.",
};
const why = (e: unknown) => (e instanceof Error ? e.message : String(e));

function Block({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="border-t border-hairline py-6">
      <h2 className="text-[13.5px] font-medium text-ink">{title}</h2>
      {hint && <p className="mt-0.5 mb-3 text-[12.5px] leading-5 text-muted">{hint}</p>}
      <div className={hint ? "" : "mt-3"}>{children}</div>
    </section>
  );
}

/** Every field saves itself 800 ms after you stop typing. */
export function PersonaEditor({ persona, onBack }: { persona: Persona; onBack(): void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(persona.name);
  const [sections, setSections] = useState<Record<string, string>>(() => ({ ...persona.sections }));
  const [style, setStyle] = useState(persona.image_style);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const patch = async (body: Patch) => {
    const saved = await api<Persona>(`/api/personas/${persona.id}`, { method: "PATCH", body: JSON.stringify(body) });
    qc.setQueryData<Persona[]>(["personas"], (list) => list?.map((p) => (p.id === saved.id ? saved : p)));
    return saved;
  };
  const { queue, cancel, state } = useAutosave<Patch>(async (body) => {
    try {
      await patch(body);
    } catch (e) {
      toast.error(why(e));
      throw e;
    }
  });
  const now = useMutation({ mutationFn: patch, onError: (e) => toast.error(why(e)) });
  const remove = useMutation({
    mutationFn: () => api<void>(`/api/personas/${persona.id}`, { method: "DELETE" }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["personas"] }); toast(`${persona.name} was moved to the trash`); onBack(); },
    onError: (e) => toast.error(why(e)),
  });
  const ordered = [...SECTIONS, ...Object.keys(sections).filter((s) => !(SECTIONS as readonly string[]).includes(s))];

  return (
    <>
      <button type="button" onClick={onBack} className="mb-6 flex items-center gap-1.5 text-[13px] text-muted hover:text-ink">
        <ArrowLeft size={14} /> Personas
      </button>
      <div className="flex items-center gap-4">
        <PersonaAvatar persona={persona} size={56} />
        <div className="min-w-0 flex-1">
          {persona.built_in ? (
            <h1 className="font-display text-[40px] leading-none">{persona.name}</h1>
          ) : (
            <input aria-label="Name" value={name} maxLength={60}
              onChange={(e) => { setName(e.target.value); if (e.target.value.trim()) queue({ name: e.target.value.trim() }); }}
              className="w-full bg-transparent font-display text-[40px] leading-none text-ink outline-none" />
          )}
          <p className="mt-1 h-4 text-[12px] italic text-faint" aria-live="polite">
            {state === "saving" ? "Saving…" : state === "saved" ? "Saved" : state === "error" ? "Not saved" : ""}
          </p>
        </div>
        <Button variant="primary" onClick={() => talkTo(persona.id)}><MessageCircle size={14} /> Talk to {persona.name}</Button>
      </div>

      {ordered.map((s) => (
        <Block key={s} title={s} hint={HINTS[s]}>
          <textarea aria-label={s} value={sections[s] ?? ""} rows={s === "Personality" ? 6 : 4}
            onChange={(e) => { setSections((cur) => ({ ...cur, [s]: e.target.value })); queue({ sections: { [s]: e.target.value } }); }}
            className="w-full rounded-lg border border-hairline bg-paper p-3 text-[13.5px] leading-6 text-ink outline-none focus:border-muted" />
        </Block>
      ))}

      {!persona.built_in && (
        <>
          <Block title="Voice" hint="Blend up to three voices. Used in calls; Azure's voice, when chosen in Settings, replaces it.">
            <VoiceBlendEditor name={persona.name} value={persona.voice} onChange={(voice) => queue({ voice })} />
          </Block>
          <Block title="Face" hint="Pick a face from four drawn from the image style below, then make a character sheet so photos keep it.">
            <Faces persona={persona} />
          </Block>
          <Block title="Photos" hint={`What ${persona.name} has sent you. A dot shows how well the face matched.`}>
            <Gallery persona={persona} />
          </Block>
          <Block title="Photos in chat" hint="How often they may send a picture of themselves. Only once they have a face.">
            <div role="radiogroup" aria-label="Photos in chat" className="inline-flex rounded-full border border-hairline p-0.5">
              {FREQUENCY.map((f) => (
                <button key={f.id} type="button" role="radio" aria-checked={persona.image_frequency === f.id}
                  onClick={() => now.mutate({ image_frequency: f.id })}
                  className={cn("rounded-full px-3 py-1 text-[12.5px]",
                    persona.image_frequency === f.id ? "bg-ink text-canvas" : "text-muted hover:text-ink")}>
                  {f.label}
                </button>
              ))}
            </div>
            <input aria-label="Image style" value={style} maxLength={200} placeholder="warm film photo, soft natural light, 35mm"
              onChange={(e) => { setStyle(e.target.value); queue({ image_style: e.target.value }); }}
              className={`${inputClass} mt-3 w-full`} />
          </Block>
          <Block title="Mood" hint="A running sense of how the conversation feels, shown as a soft glow and felt in the replies.">
            <Switch label="Tone engine" checked={persona.tone_engine} onCheckedChange={(v) => now.mutate({ tone_engine: v })} />
          </Block>
          <Block title="Accent" hint={persona.accent?.source === "anchor" ? "Taken from the portrait." : "The colour the app takes on while you talk."}>
            <div className="flex items-center gap-3">
              <span className="size-7 rounded-full border border-hairline" style={{ background: persona.accent?.light ?? "var(--accent)" }} />
              <label className="text-[12.5px] text-muted">
                Pick manually{" "}
                <input type="color" aria-label="Accent colour" value={persona.accent?.light ?? "#c2553a"}
                  onChange={(e) => now.mutate({ accent_pick: e.target.value })} className="ml-1 h-7 w-10 align-middle" />
              </label>
              {persona.accent?.source === "manual" && (
                <Button onClick={() => now.mutate({ accent_pick: null })}>Reset</Button>
              )}
            </div>
          </Block>
          <Block title="Way of writing" hint={`Learn ${persona.name}'s style from a real chat: a short directive, and their messages as examples.`}>
            <StyleImport persona={persona} />
          </Block>
          <section className="border-t border-hairline py-6">
            {confirmDelete ? (
              <div className="flex items-center gap-3">
                <span className="text-[13px] text-ink">Delete {persona.name}? The folder moves to the trash.</span>
                <Button variant="danger" disabled={remove.isPending} onClick={() => { cancel(); remove.mutate(); }}>Delete</Button>
                <Button onClick={() => setConfirmDelete(false)}>Keep</Button>
              </div>
            ) : (
              <Button variant="danger" onClick={() => setConfirmDelete(true)}>Delete persona</Button>
            )}
          </section>
        </>
      )}
      {persona.built_in && (
        <Block title="Voice" hint="Aethel's voice is set in Settings → Voice.">
          <span />
        </Block>
      )}
    </>
  );
}
