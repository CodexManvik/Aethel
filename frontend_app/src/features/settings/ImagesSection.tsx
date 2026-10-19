import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../../lib/api";
import { browseFile, canBrowse } from "../../lib/browse";
import { IMAGES_OPENAI_KEY, saveKey } from "../../lib/keys";
import type { AppSettings } from "../../lib/types";
import { Button } from "../../ui/Button";
import { Field, inputClass } from "../../ui/Field";
import { useImagesStatus } from "../personas/Faces";
import { Section } from "./Section";
import { useUpdateSettings } from "./useSettings";

const selectClass = "h-9 w-64 rounded-lg border border-hairline bg-paper px-2 text-[13px] text-ink outline-none focus:border-muted";
const why = (e: unknown) => (e instanceof Error && e.message ? e.message : "That didn't work.");

const LABELS: [RegExp, string][] = [[/^gemini:/, "Gemini "], [/^openrouter:/, "OpenRouter "], [/^openai:/, "your image server: "],
  [/^comfyui:/, "ComfyUI: "], [/^local:/, "this PC: "]];

export function describeImages(s: { backend: string | null; why_not: string; face_models: boolean } | undefined): string {
  if (!s || typeof s !== "object") return "";
  const name = s.backend ? LABELS.reduce((text, [re, label]) => text.replace(re, label), s.backend) : "";
  const who = s.backend ? `Photos are made with ${name}.` : s.why_not;
  return `${who} ${s.face_models ? "The face check is installed." : "The face check isn't installed (Personas → Face)."}`;
}

interface Check {
  ok: boolean;
  placeholders: string[];
  error: string | null;
}

function Browse({ onPick }: { onPick(): void }) {
  return canBrowse() ? <Button onClick={onPick}>Browse…</Button> : null;
}

export function ImagesSection({ settings }: { settings: AppSettings }) {
  const qc = useQueryClient();
  const update = useUpdateSettings();
  const { data: status } = useImagesStatus();
  const images = settings.images;
  const [router, setRouter] = useState(images?.openrouter_model ?? "");
  const [openai, setOpenai] = useState({ base_url: images?.openai?.base_url ?? "", model: images?.openai?.model ?? "" });
  const [openaiKey, setOpenaiKey] = useState("");
  const [comfy, setComfy] = useState({ base_url: images?.comfyui?.base_url ?? "", workflow_path: images?.comfyui?.workflow_path ?? "" });
  const [check, setCheck] = useState<Check | null>(null);
  const [localDir, setLocalDir] = useState(images?.local_dir ?? "");
  const [listing, setListing] = useState(false);
  const { data: models, refetch } = useQuery({ queryKey: ["images", "gemini-models"], enabled: false,
    queryFn: () => api<string[]>("/api/images/gemini-models") });
  const save = (patch: Partial<AppSettings["images"]>, done?: string) =>
    update.mutate({ images: patch }, {
      onSuccess: () => { if (done) toast(done); void qc.invalidateQueries({ queryKey: ["images", "status"] }); },
      onError: (e) => toast.error(why(e)),
    });
  const gemini = images?.gemini_model ?? "";
  const options = Array.from(new Set([gemini, ...(Array.isArray(models) ? models : [])])).filter(Boolean);
  const runCheck = async (path = comfy.workflow_path) => {
    try {
      setCheck(await api<Check>("/api/images/comfyui/check", { method: "POST", body: JSON.stringify({ path }) }));
    } catch (e) {
      toast.error(why(e));
    }
  };

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

      <Field label="Your image server" hint="Any OpenAI-compatible images API (OpenAI, LocalAI, a proxy). On this PC (127.0.0.1) it also works in private mode." htmlFor="openai-image-url">
        <div className="flex flex-col items-end gap-2">
          <div className="flex gap-2">
            <input id="openai-image-url" aria-label="Image server URL" className={`${inputClass} w-64`} value={openai.base_url}
              placeholder="https://api.openai.com/v1" onChange={(e) => setOpenai((o) => ({ ...o, base_url: e.target.value }))} />
            <input aria-label="Image server model" className={`${inputClass} w-40`} value={openai.model} placeholder="gpt-image-1"
              onChange={(e) => setOpenai((o) => ({ ...o, model: e.target.value.trim() }))} />
          </div>
          <div className="flex gap-2">
            <input aria-label="Image server key" type="password" autoComplete="off" className={`${inputClass} w-64`} value={openaiKey}
              placeholder="Key (optional)" onChange={(e) => setOpenaiKey(e.target.value)} />
            <Button onClick={async () => {
              if (openaiKey.trim()) { await saveKey(IMAGES_OPENAI_KEY, openaiKey.trim()); setOpenaiKey(""); }
              save({ openai: { base_url: openai.base_url.trim(), model: openai.model } }, "Image server saved");
            }}>Save</Button>
          </div>
        </div>
      </Field>

      <Field label="ComfyUI" hint="Your own workflow, exported with Workflow → Export (API). Mark the inputs with {{prompt}}, and {{reference}} on a Load Image node to keep the face." htmlFor="comfy-url">
        <div className="flex flex-col items-end gap-2">
          <input id="comfy-url" aria-label="ComfyUI URL" className={`${inputClass} w-80`} value={comfy.base_url}
            placeholder="http://127.0.0.1:8188" onChange={(e) => setComfy((c) => ({ ...c, base_url: e.target.value }))} />
          <div className="flex gap-2">
            <input aria-label="ComfyUI workflow" className={`${inputClass} w-64`} value={comfy.workflow_path}
              placeholder={"D:\\comfy\\aethel-api.json"} onChange={(e) => setComfy((c) => ({ ...c, workflow_path: e.target.value }))} />
            <Browse onPick={async () => {
              const picked = await browseFile("Choose the workflow (API format)", [{ name: "ComfyUI workflow", extensions: ["json"] }]);
              if (picked) { setComfy((c) => ({ ...c, workflow_path: picked })); void runCheck(picked); }
            }} />
          </div>
          <div className="flex gap-2">
            <Button disabled={!comfy.workflow_path} onClick={() => void runCheck()}>Check</Button>
            <Button onClick={() => save({ comfyui: { base_url: comfy.base_url.trim(), workflow_path: comfy.workflow_path.trim() } }, "ComfyUI saved")}>Save</Button>
          </div>
          {check && (
            <p role="status" className={`text-[12px] ${check.ok ? "text-muted" : "text-accent"}`}>
              {check.ok ? `Uses ${check.placeholders.map((p) => `{{${p}}}`).join(", ")}${check.placeholders.includes("reference") ? "" : " (text only: faces may vary)"}.`
                : check.error}
            </p>
          )}
        </div>
      </Field>

      <Field label="Local model folder" hint="Where the Z-Image Turbo weights are. Empty uses models/image." htmlFor="local-image-dir">
        <div className="flex gap-2">
          <input id="local-image-dir" className={`${inputClass} w-64`} value={localDir} placeholder={"D:\\models\\z-image"}
            onChange={(e) => setLocalDir(e.target.value)} />
          <Browse onPick={async () => { const picked = await browseFile("Choose the Z-Image folder", undefined, true); if (picked) setLocalDir(picked); }} />
          <Button disabled={localDir === (images?.local_dir ?? "")} onClick={() => save({ local_dir: localDir.trim() }, "Local model folder saved")}>Save</Button>
        </div>
      </Field>

      <Field label="OpenRouter image model" hint="Leave empty to keep OpenRouter off." htmlFor="openrouter-image-model">
        <div className="flex items-center gap-2">
          <input id="openrouter-image-model" aria-label="OpenRouter image model" className={`${inputClass} w-64`} value={router}
            placeholder="google/gemini-3.1-flash-image" onChange={(e) => setRouter(e.target.value.trim())} />
          <Button disabled={router === (images?.openrouter_model ?? "")} onClick={() => save({ openrouter_model: router })}>Save</Button>
        </div>
      </Field>
    </Section>
  );
}
