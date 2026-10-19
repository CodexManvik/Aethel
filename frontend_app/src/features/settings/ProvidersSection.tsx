import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { api } from "../../lib/api";
import { Button } from "../../ui/Button";
import { Field, inputClass } from "../../ui/Field";
import { endpointKey, saveKey, type KeyProvider } from "../../lib/keys";
import type { AppSettings, Endpoint, ProviderInfo } from "../../lib/types";
import { Section } from "./Section";
import { useProviders, useUpdateSettings } from "./useSettings";

const HINTS: Record<string, string> = {
  groq: "Very fast chat models. console.groq.com/keys",
  gemini: "Planning and vision. aistudio.google.com/apikey",
  openrouter: "One key, many models. openrouter.ai/keys",
};

function KeyRow({ provider }: { provider: ProviderInfo }) {
  const [value, setValue] = useState("");
  const qc = useQueryClient();
  const id = (provider.key_name || provider.id) as KeyProvider;
  const done = (msg: string) => {
    setValue("");
    toast(msg);
    void qc.invalidateQueries({ queryKey: ["providers"] });
    void qc.invalidateQueries({ queryKey: ["models", provider.id] });
  };
  return (
    <div data-testid={`key-row-${provider.id}`}>
      <Field label={provider.label} hint={HINTS[provider.id]} htmlFor={`key-${provider.id}`}>
        <div className="flex items-center gap-2">
          <span className={provider.has_key ? "size-1.5 rounded-full bg-accent" : "size-1.5 rounded-full bg-hairline"}
            title={provider.has_key ? "Key saved" : provider.key_optional ? "No key (optional)" : "No key"} />
          <input
            id={`key-${provider.id}`}
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
  const url = text.match(/Value error, (must start[^"]+)/);
  if (url) return `That URL doesn't look right: it ${url[1]}.`;
  const other = text.match(/Value error, ([^"]+)/);
  return other ? `Couldn't save: ${other[1]}.` : `Couldn't save: ${text}`;
}

/** A slug for a new endpoint's id, unique among the others: "LM Studio" → "lm-studio", then "lm-studio-2". */
export function endpointId(name: string, taken: string[]): string {
  const reserved = ["groq", "gemini", "openrouter", "local", ...taken];
  let base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^[^a-z]+|-+$/g, "").slice(0, 28) || "endpoint";
  if (base.length < 2) base = `${base}-endpoint`;
  let id = base;
  for (let n = 2; reserved.includes(id); n++) id = `${base}-${n}`;
  return id;
}

function EndpointRow({ endpoint, provider, onSave, onRemove }: {
  endpoint: Endpoint;
  provider?: ProviderInfo;
  onSave(next: Endpoint): void;
  onRemove(): void;
}) {
  const [url, setUrl] = useState(endpoint.base_url);
  const [listing, setListing] = useState(false);
  const list = async () => {
    setListing(true);
    try {
      const { models } = await api<{ models: string[] }>(`/api/providers/${endpoint.id}/models`);
      toast(models.length ? `${endpoint.name}: ${models.length} models (${models.slice(0, 3).join(", ")}${models.length > 3 ? "…" : ""})`
        : `${endpoint.name} answered, but listed no models.`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setListing(false);
    }
  };
  return (
    <div className="border-t border-hairline py-3 first:border-t-0" data-testid={`endpoint-${endpoint.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-32 truncate text-[13.5px] font-medium text-ink" title={endpoint.id}>{endpoint.name}</span>
        <input aria-label={`${endpoint.name} URL`} className={`${inputClass} w-72`} value={url}
          placeholder="http://127.0.0.1:1234/v1" onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") onSave({ ...endpoint, base_url: url.trim() }); }} />
        <Button disabled={url.trim() === endpoint.base_url} onClick={() => onSave({ ...endpoint, base_url: url.trim() })}>Save</Button>
        <Button disabled={listing || !endpoint.base_url} onClick={() => void list()}>{listing ? "Asking…" : "List models"}</Button>
        <Button variant="danger" onClick={onRemove}>Remove</Button>
      </div>
      {provider && <div className="mt-1"><KeyRow provider={{ ...provider, label: `${endpoint.name} key` }} /></div>}
    </div>
  );
}

function AddEndpoint({ taken, onAdd }: { taken: string[]; onAdd(e: Endpoint, key: string): void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [key, setKey] = useState("");
  if (!open) return <Button className="mt-3" onClick={() => setOpen(true)}><Plus size={14} /> Add endpoint</Button>;
  const add = () => {
    onAdd({ id: endpointId(name, taken), name: name.trim(), base_url: url.trim() }, key.trim());
    setOpen(false); setName(""); setUrl(""); setKey("");
  };
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-hairline p-3">
      <input aria-label="Endpoint name" className={`${inputClass} w-40`} placeholder="LM Studio" value={name} onChange={(e) => setName(e.target.value)} />
      <input aria-label="Endpoint URL" className={`${inputClass} w-72`} placeholder="http://127.0.0.1:1234/v1" value={url} onChange={(e) => setUrl(e.target.value)} />
      <input aria-label="Endpoint key" type="password" autoComplete="off" className={`${inputClass} w-44`} placeholder="Key (optional)" value={key} onChange={(e) => setKey(e.target.value)} />
      <Button variant="primary" disabled={!name.trim() || !url.trim()} onClick={add}>Add</Button>
      <Button onClick={() => setOpen(false)}>Cancel</Button>
    </div>
  );
}

export function ProvidersSection({ settings }: { settings: AppSettings }) {
  const { data: providers = [] } = useProviders();
  const update = useUpdateSettings();
  const qc = useQueryClient();
  const endpoints = settings.endpoints ?? [];
  const refresh = (id?: string) => {
    void qc.invalidateQueries({ queryKey: ["providers"] });
    if (id) void qc.invalidateQueries({ queryKey: ["models", id] });
  };
  const saveEndpoints = (next: Endpoint[], done: string, id?: string, after?: () => Promise<void>) =>
    update.mutate({ endpoints: next }, {
      onSuccess: async () => { if (after) await after(); toast(done); refresh(id); },
      onError: (err) => toast.error(saveError(err)),
    });
  return (
    <Section title="Providers" description="Keys are kept in Windows Credential Manager and only ever held in memory by Aethel.">
      {providers.filter((p) => p.needs_key && !p.endpoint).map((p) => <KeyRow key={p.id} provider={p} />)}
      <div className="py-4">
        <h3 className="text-[13.5px] font-medium text-ink">Your endpoints</h3>
        <p className="mt-0.5 mb-2 text-[12.5px] leading-5 text-muted">
          Any OpenAI-compatible server: LM Studio, Ollama, vLLM, llama.cpp on another PC, or a hosted API. Each one can
          be used in any role below. Local servers usually need no key.
        </p>
        {endpoints.map((e) => (
          <EndpointRow key={e.id} endpoint={e} provider={providers.find((p) => p.id === e.id)}
            onSave={(next) => saveEndpoints(endpoints.map((x) => (x.id === e.id ? next : x)), `${e.name} saved`, e.id)}
            onRemove={() => saveEndpoints(endpoints.filter((x) => x.id !== e.id), `${e.name} removed`, e.id,
              () => saveKey(endpointKey(e.id), null).catch(() => {}))} />
        ))}
        <AddEndpoint taken={endpoints.map((e) => e.id)} onAdd={(e, key) =>
          saveEndpoints([...endpoints, e], `${e.name} added`, e.id, async () => { if (key) await saveKey(endpointKey(e.id), key); })} />
      </div>
    </Section>
  );
}
