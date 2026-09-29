import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "../../ui/Button";
import { Field, inputClass } from "../../ui/Field";
import { saveKey, type KeyProvider } from "../../lib/keys";
import type { AppSettings, ProviderInfo } from "../../lib/types";
import { Section } from "./Section";
import { useProviders, useUpdateSettings } from "./useSettings";

const HINTS: Record<KeyProvider, string> = {
  groq: "Very fast chat models. console.groq.com/keys",
  gemini: "Planning and vision. aistudio.google.com/apikey",
  openrouter: "One key, many models. openrouter.ai/keys",
  custom: "Any OpenAI-compatible server. Local servers (llama.cpp, LM Studio, Ollama) usually need no key.",
};

function KeyRow({ provider }: { provider: ProviderInfo }) {
  const [value, setValue] = useState("");
  const qc = useQueryClient();
  const id = provider.id as KeyProvider;
  const done = (msg: string) => {
    setValue("");
    toast(msg);
    void qc.invalidateQueries({ queryKey: ["providers"] });
    void qc.invalidateQueries({ queryKey: ["models", id] });
  };
  return (
    <div data-testid={`key-row-${id}`}>
      <Field label={provider.label} hint={HINTS[id]} htmlFor={`key-${id}`}>
        <div className="flex items-center gap-2">
          <span className={provider.has_key ? "size-1.5 rounded-full bg-accent" : "size-1.5 rounded-full bg-hairline"}
            title={provider.has_key ? "Key saved" : provider.key_optional ? "No key (optional)" : "No key"} />
          <input
            id={`key-${id}`}
            aria-label={`${provider.label} API key`}
            type="password"
            autoComplete="off"
            placeholder={provider.has_key ? "•••••••• saved" : provider.key_optional ? "Optional API key" : "Paste API key"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className={`${inputClass} w-56`}
          />
          <Button onClick={async () => { await saveKey(id, value.trim()); done(`${provider.label} key saved`); }} disabled={!value.trim()}>
            Save
          </Button>
          {provider.has_key && (
            <Button variant="danger" onClick={async () => { await saveKey(id, null); done(`${provider.label} key removed`); }}>
              Remove
            </Button>
          )}
        </div>
      </Field>
    </div>
  );
}

/** "must start with http://…" out of FastAPI's 422 detail, or the message as is. */
export function saveError(err: unknown): string {
  const text = err instanceof Error ? err.message : String(err);
  const m = text.match(/Value error, ([^"]+)/);
  return m ? `That URL doesn't look right: it ${m[1]}.` : `Couldn't save: ${text}`;
}

export function ProvidersSection({ settings }: { settings: AppSettings }) {
  const { data: providers = [] } = useProviders();
  const update = useUpdateSettings();
  const qc = useQueryClient();
  const [baseUrl, setBaseUrl] = useState(settings.custom_base_url);
  const saveUrl = () =>
    update.mutate({ custom_base_url: baseUrl.trim() }, {
      onSuccess: (next) => {
        setBaseUrl(next.custom_base_url);
        toast(next.custom_base_url ? `Custom endpoint saved: ${next.custom_base_url}` : "Custom endpoint cleared.");
        void qc.invalidateQueries({ queryKey: ["providers"] });
        void qc.invalidateQueries({ queryKey: ["models", "custom"] });  // list the new server's models
      },
      onError: (err) => toast(saveError(err)),
    });
  return (
    <Section title="Providers" description="Keys are kept in Windows Credential Manager and only ever held in memory by Aethel.">
      {providers.filter((p) => p.needs_key).map((p) => <KeyRow key={p.id} provider={p} />)}
      <Field label="Custom endpoint URL" hint="For the Custom provider, e.g. http://192.168.1.20:8080/v1" htmlFor="custom-url">
        <div className="flex gap-2">
          <input id="custom-url" className={inputClass} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") saveUrl(); }} placeholder="http://127.0.0.1:1234/v1" />
          <Button onClick={saveUrl} disabled={update.isPending || baseUrl.trim() === settings.custom_base_url}>
            {update.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      </Field>
    </Section>
  );
}
