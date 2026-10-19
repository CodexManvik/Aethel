import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../../lib/api";
import type { AppSettings } from "../../lib/types";
import { Button } from "../../ui/Button";
import { Field, inputClass } from "../../ui/Field";
import { useImagesStatus } from "../personas/Faces";
import { Section } from "./Section";
import { useUpdateSettings } from "./useSettings";

const selectClass = "h-9 w-64 rounded-lg border border-hairline bg-paper px-2 text-[13px] text-ink outline-none focus:border-muted";
const why = (e: unknown) => (e instanceof Error && e.message ? e.message : "That didn't work.");

export function describeImages(s: { backend: string | null; why_not: string; face_models: boolean } | undefined): string {
  if (!s) return "";
  const who = s.backend ? `Photos are made with ${s.backend.replace(/^gemini:/, "Gemini ").replace(/^openrouter:/, "OpenRouter ")
    .replace(/^local:/, "this PC: ")}.` : s.why_not;
  return `${who} ${s.face_models ? "The face check is installed." : "The face check isn't installed (Personas → Face)."}`;
}

export function ImagesSection({ settings }: { settings: AppSettings }) {
  const qc = useQueryClient();
  const update = useUpdateSettings();
  const { data: status } = useImagesStatus();
  const [router, setRouter] = useState(settings.images?.openrouter_model ?? "");
  const [listing, setListing] = useState(false);
  const { data: models, refetch } = useQuery({ queryKey: ["images", "gemini-models"], enabled: false,
    queryFn: () => api<string[]>("/api/images/gemini-models") });
  const save = (patch: Partial<AppSettings["images"]>) =>
    update.mutate({ images: patch }, {
      onSuccess: () => void qc.invalidateQueries({ queryKey: ["images", "status"] }),
      onError: (e) => toast.error(why(e)),
    });
  const gemini = settings.images?.gemini_model ?? "";
  const options = Array.from(new Set([gemini, ...(Array.isArray(models) ? models : [])])).filter(Boolean);

  return (
    <Section title="Images" description={describeImages(status) || "Photos your personas send. Never in private mode unless a model runs on this PC."}>
      <Field label="Gemini image model" hint="List shows the image models your Gemini key can use." htmlFor="gemini-image-model">
        <div className="flex items-center gap-2">
          <select id="gemini-image-model" aria-label="Gemini image model" className={selectClass} value={gemini}
            onChange={(e) => save({ gemini_model: e.target.value })}>
            {options.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
          <Button disabled={listing} onClick={() => { setListing(true); void refetch().then((r) => {
            setListing(false);
            if (r.error) toast.error(why(r.error));
            else if (!r.data?.length) toast("No image models came back. Is a Gemini key saved?");
          }); }}>List</Button>
        </div>
      </Field>
      <Field label="OpenRouter image model" hint="Used when Gemini can't be. Leave empty to keep OpenRouter off." htmlFor="openrouter-image-model">
        <div className="flex items-center gap-2">
          <input id="openrouter-image-model" aria-label="OpenRouter image model" className={`${inputClass} w-64`} value={router}
            placeholder="google/gemini-3.1-flash-image" onChange={(e) => setRouter(e.target.value.trim())} />
          <Button disabled={router === (settings.images?.openrouter_model ?? "")} onClick={() => save({ openrouter_model: router })}>Save</Button>
        </div>
      </Field>
    </Section>
  );
}
