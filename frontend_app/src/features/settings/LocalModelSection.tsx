import { useState, type ChangeEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../../lib/api";
import { browseFile, canBrowse } from "../../lib/browse";
import type { AppSettings, LocalLLMSettings } from "../../lib/types";
import { Button } from "../../ui/Button";
import { Field, inputClass } from "../../ui/Field";
import { DownloadButton } from "./Downloads";
import { Section } from "./Section";
import { useUpdateSettings } from "./useSettings";

export interface LocalStatus {
  model: string | null;
  mmproj: string | null;
  server: string | null;
  running: boolean;
  why_not: string | null;
}

const base = (p: string) => p.split(/[\\/]/).pop() ?? p;

export function describeLocal(s: LocalStatus | undefined): string {
  if (!s || typeof s !== "object") return "";
  if (s.why_not) return s.why_not;
  if (typeof s.model !== "string") return ""; // not known (yet)
  const vision = s.mmproj ? " (with vision)" : "";
  return `${s.running ? "Running" : "Ready"}: ${base(s.model!)}${vision} with ${s.server}.`;
}

function PathField({ id, label, hint, value, placeholder, onChange, onBrowse }: {
  id: string; label: string; hint: string; value: string; placeholder: string;
  onChange(v: string): void; onBrowse(): void;
}) {
  return (
    <Field label={label} hint={hint} htmlFor={id}>
      <div className="flex gap-2">
        <input id={id} className={inputClass} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
        {canBrowse() && <Button onClick={onBrowse}>Browse…</Button>}
      </div>
    </Field>
  );
}

export function LocalModelSection({ settings }: { settings: AppSettings }) {
  const [local, setLocal] = useState<LocalLLMSettings>({ ...settings.local_llm, server_path: settings.local_llm.server_path ?? "" });
  const update = useUpdateSettings();
  const qc = useQueryClient();
  const { data: status } = useQuery({ queryKey: ["local-status"], queryFn: () => api<LocalStatus>("/api/providers/local/status") });
  const num = (key: keyof LocalLLMSettings) => (e: ChangeEvent<HTMLInputElement>) =>
    setLocal((l) => ({ ...l, [key]: Number(e.target.value) || 0 }));
  const pick = async (key: "model_path" | "server_path") => {
    const picked = key === "model_path"
      ? await browseFile("Choose the model (.gguf)", [{ name: "GGUF model", extensions: ["gguf"] }])
      : await browseFile("Choose llama-server", [{ name: "llama-server", extensions: ["exe"] }]);
    if (picked) setLocal((l) => ({ ...l, [key]: picked }));
  };
  // the download sets server_path itself: show it, and the new status
  const refreshAfterDownload = () => {
    void qc.invalidateQueries({ queryKey: ["local-status"] });
    void qc.invalidateQueries({ queryKey: ["settings"] });
    void api<AppSettings>("/api/settings").then((s) =>
      setLocal((l) => ({ ...l, server_path: s.local_llm?.server_path ?? l.server_path }))).catch(() => {});
  };
  const save = () => update.mutate({ local_llm: local }, {
    onSuccess: () => { toast("Local model settings saved. They apply the next time it starts."); void qc.invalidateQueries({ queryKey: ["local-status"] }); },
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  });

  return (
    <Section title="Local model" description={describeLocal(status) || "Used in private mode and as a last-resort fallback."}>
      <PathField id="local-path" label="Model file" hint="A .gguf anywhere. Empty uses the largest .gguf in models/llm."
        value={local.model_path} placeholder={"D:\\models\\my-model.gguf"}
        onChange={(v) => setLocal((l) => ({ ...l, model_path: v }))} onBrowse={() => void pick("model_path")} />
      <PathField id="local-server" label="llama-server" hint="The llama.cpp server program (or its folder). Empty looks on PATH."
        value={local.server_path} placeholder={"D:\\llama.cpp\\llama-server.exe"}
        onChange={(v) => setLocal((l) => ({ ...l, server_path: v }))} onBrowse={() => void pick("server_path")} />
      {status && typeof status === "object" && status.server === null && (
        <Field label="Get llama-server" hint={`llama.cpp ${"b11381"} from its GitHub release, checked against a pinned hash. The GPU build works with NVIDIA, AMD and Intel graphics (Vulkan); the CPU build works anywhere. Once downloaded, it becomes the llama-server above.`}>
          <div className="flex gap-2">
            <DownloadButton name="llama-vulkan" label="GPU (33 MB)" onDone={refreshAfterDownload} />
            <DownloadButton name="llama-cpu" label="CPU only (19 MB)" onDone={refreshAfterDownload} />
          </div>
        </Field>
      )}
      <Field label="Context size" hint="0 uses the model's own trained context." htmlFor="local-ctx">
        <input id="local-ctx" type="number" min={0} className={`${inputClass} w-28`} value={local.context_size} onChange={num("context_size")} />
      </Field>
      <Field label="CPU threads" htmlFor="local-threads">
        <input id="local-threads" type="number" min={1} className={`${inputClass} w-28`} value={local.threads} onChange={num("threads")} />
      </Field>
      <Field label="GPU layers" hint="99 puts everything on the GPU; 0 runs on CPU." htmlFor="local-gpu">
        <input id="local-gpu" type="number" min={0} className={`${inputClass} w-28`} value={local.gpu_layers} onChange={num("gpu_layers")} />
      </Field>
      <div className="py-4">
        <Button variant="primary" onClick={save}>Save</Button>
      </div>
    </Section>
  );
}
