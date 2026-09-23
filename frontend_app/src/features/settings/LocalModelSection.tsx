import { useState, type ChangeEvent } from "react";
import { toast } from "sonner";
import type { AppSettings, LocalLLMSettings } from "../../lib/types";
import { Button } from "../../ui/Button";
import { Field, inputClass } from "../../ui/Field";
import { Section } from "./Section";
import { useUpdateSettings } from "./useSettings";

export function LocalModelSection({ settings }: { settings: AppSettings }) {
  const [local, setLocal] = useState<LocalLLMSettings>(settings.local_llm);
  const update = useUpdateSettings();
  const num = (key: keyof LocalLLMSettings) => (e: ChangeEvent<HTMLInputElement>) =>
    setLocal((l) => ({ ...l, [key]: Number(e.target.value) || 0 }));

  return (
    <Section title="Local model" description="Used in private mode and as a last-resort fallback. Put a .gguf file in models/llm, or point to one anywhere.">
      <Field label="Model file" hint="Leave empty to use the largest .gguf in models/llm." htmlFor="local-path">
        <input id="local-path" className={inputClass} value={local.model_path} placeholder={"C:\\models\\my-model.gguf"} onChange={(e) => setLocal((l) => ({ ...l, model_path: e.target.value }))} />
      </Field>
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
        <Button variant="primary" onClick={() => update.mutate({ local_llm: local }, { onSuccess: () => toast("Local model settings saved. They apply the next time it starts.") })}>
          Save
        </Button>
      </div>
    </Section>
  );
}
