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
  custom: "Any OpenAI-compatible server.",
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
          <span className={provider.has_key ? "size-1.5 rounded-full bg-accent" : "size-1.5 rounded-full bg-hairline"} title={provider.has_key ? "Key saved" : "No key"} />
          <input
            id={`key-${id}`}
            aria-label={`${provider.label} API key`}
            type="password"
            autoComplete="off"
            placeholder={provider.has_key ? "•••••••• saved" : "Paste API key"}
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

export function ProvidersSection({ settings }: { settings: AppSettings }) {
  const { data: providers = [] } = useProviders();
  const update = useUpdateSettings();
  const [baseUrl, setBaseUrl] = useState(settings.custom_base_url);
  return (
    <Section title="Providers" description="Keys are kept in Windows Credential Manager and only ever held in memory by Aethel.">
      {providers.filter((p) => p.needs_key).map((p) => <KeyRow key={p.id} provider={p} />)}
      <Field label="Custom endpoint URL" hint="For the Custom provider, e.g. http://192.168.1.20:8080/v1" htmlFor="custom-url">
        <div className="flex gap-2">
          <input id="custom-url" className={inputClass} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://…/v1" />
          <Button onClick={() => update.mutate({ custom_base_url: baseUrl.trim() })}>Save</Button>
        </div>
      </Field>
    </Section>
  );
}
