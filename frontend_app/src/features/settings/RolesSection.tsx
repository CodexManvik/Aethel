import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { api } from "../../lib/api";
import type { AppSettings, ProviderId, ProviderInfo, ProviderTestResult, RouteEntry } from "../../lib/types";
import { Button } from "../../ui/Button";
import { IconButton } from "../../ui/IconButton";
import { inputClass } from "../../ui/Field";
import { Section } from "./Section";
import { useModels, useProviders, useUpdateSettings } from "./useSettings";

// Stable identity: a fresh [] each render would re-trigger RoleEditor's reset effect forever.
const NO_ENTRIES: RouteEntry[] = [];

const ROLES: { id: string; label: string; hint: string }[] = [
  { id: "chat", label: "Conversation", hint: "Everyday talking. Fast models feel best here." },
  { id: "agent", label: "Agent", hint: "Plans and carries out tasks (Phase 1)." },
  { id: "vision", label: "Vision", hint: "Reads screenshots when an app can't be read directly (Phase 1)." },
];

function ModelOptions({ provider }: { provider: ProviderInfo }) {
  const { data = [] } = useModels(provider.id, provider.has_key);
  return (
    <datalist id={`models-${provider.id}`}>
      {data.map((m) => <option key={m} value={m} />)}
    </datalist>
  );
}

function EntryRow({ entry, providers, onChange, onMove, onRemove, canUp, canDown }: {
  entry: RouteEntry;
  providers: ProviderInfo[];
  onChange: (e: RouteEntry) => void;
  onMove: (dir: -1 | 1) => void;
  onRemove: () => void;
  canUp: boolean;
  canDown: boolean;
}) {
  const [testing, setTesting] = useState(false);
  const test = async () => {
    setTesting(true);
    try {
      const r = await api<ProviderTestResult>("/api/providers/test", { method: "POST", body: JSON.stringify(entry) });
      if (r.ok) toast(`${entry.provider}:${entry.model} replied in ${r.latency_ms} ms`);
      else toast.error(r.error ?? "Test failed");
    } finally {
      setTesting(false);
    }
  };
  return (
    <div className="flex items-center gap-2 py-2">
      <select
        aria-label="Provider"
        value={entry.provider}
        onChange={(e) => onChange({ ...entry, provider: e.target.value as ProviderId })}
        className="h-9 rounded-lg border border-hairline bg-paper px-2 text-[13px] text-ink"
      >
        {providers.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
      </select>
      <input
        aria-label="Model"
        list={`models-${entry.provider}`}
        value={entry.model}
        onChange={(e) => onChange({ ...entry, model: e.target.value })}
        className={`${inputClass} w-64`}
      />
      <Button onClick={test} disabled={testing || !entry.model.trim()}>{testing ? "Testing…" : "Test"}</Button>
      <IconButton label="Move up" disabled={!canUp} onClick={() => onMove(-1)}><ArrowUp size={14} /></IconButton>
      <IconButton label="Move down" disabled={!canDown} onClick={() => onMove(1)}><ArrowDown size={14} /></IconButton>
      <IconButton label="Remove" onClick={onRemove}><X size={14} /></IconButton>
    </div>
  );
}

function RoleEditor({ role, label, hint, initial, providers }: {
  role: string; label: string; hint: string; initial: RouteEntry[]; providers: ProviderInfo[];
}) {
  const [draft, setDraft] = useState<RouteEntry[]>(initial);
  const update = useUpdateSettings();
  useEffect(() => setDraft(initial), [initial]);

  const move = (i: number, dir: -1 | 1) =>
    setDraft((d) => {
      const next = [...d];
      [next[i], next[i + dir]] = [next[i + dir], next[i]];
      return next;
    });

  return (
    <div data-testid={`role-${role}`} className="py-4">
      <p className="text-[13.5px] font-medium text-ink">{label}</p>
      <p className="mb-2 text-[12.5px] text-muted">{hint} The first model answers; the others take over if it's unavailable.</p>
      {draft.map((entry, i) => (
        <EntryRow
          key={i}
          entry={entry}
          providers={providers}
          onChange={(e) => setDraft((d) => d.map((x, j) => (j === i ? e : x)))}
          onMove={(dir) => move(i, dir)}
          onRemove={() => setDraft((d) => d.filter((_, j) => j !== i))}
          canUp={i > 0}
          canDown={i < draft.length - 1}
        />
      ))}
      <div className="mt-2 flex gap-2">
        <Button onClick={() => setDraft((d) => [...d, { provider: "local", model: "local" }])}>
          <Plus size={14} /> Add fallback
        </Button>
        <Button
          variant="primary"
          aria-label={`Save ${role} models`}
          disabled={draft.some((e) => !e.model.trim())}
          onClick={() => update.mutate({ roles: { [role]: draft.map((e) => ({ ...e, model: e.model.trim() })) } }, { onSuccess: () => toast(`${label} models saved`) })}
        >
          Save
        </Button>
      </div>
    </div>
  );
}

export function RolesSection({ settings }: { settings: AppSettings }) {
  const { data: providers = [] } = useProviders();
  return (
    <Section title="Models" description="Choose which model does each job, with fallbacks in order.">
      {providers.map((p) => <ModelOptions key={p.id} provider={p} />)}
      {ROLES.map((r) => (
        <RoleEditor key={r.id} role={r.id} label={r.label} hint={r.hint} initial={settings.roles[r.id] ?? NO_ENTRIES} providers={providers} />
      ))}
    </Section>
  );
}
