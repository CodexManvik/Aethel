import { toast } from "sonner";
import { Button } from "../../ui/Button";
import { Field } from "../../ui/Field";
import { Switch } from "../../ui/Switch";
import type { AppSettings } from "../../lib/types";
import { useEpisodicStatus, useRebuildEpisodic } from "../memory/memoryApi";
import { Section } from "./Section";
import { useUpdateSettings } from "./useSettings";

export function MemorySection({ settings }: { settings: AppSettings }) {
  const update = useUpdateSettings();
  const rebuild = useRebuildEpisodic();
  const { data: status } = useEpisodicStatus();
  const m = settings.memory;
  return (
    <Section title="Memory" description="What Aethel remembers about you stays on this computer. You can see and change all of it in Memory.">
      <Field label="Remember facts about me" hint="After you mention something about yourself, Aethel keeps a short note. Every change shows under your message with Undo.">
        <Switch label="Remember facts about me" checked={m.facts_enabled}
          onCheckedChange={(v) => update.mutate({ memory: { facts_enabled: v } })} />
      </Field>
      <Field label="Recall earlier conversations" hint="Aethel can bring up something you talked about before, in any conversation.">
        <Switch label="Recall earlier conversations" checked={m.episodic_enabled}
          onCheckedChange={(v) => update.mutate({ memory: { episodic_enabled: v } })} />
      </Field>
      <Field label="Earlier conversations" hint={status ? `${status.indexed} of ${status.exchanges} exchanges indexed.` : "Checking…"}>
        <Button
          disabled={!status || status.rebuilding || rebuild.isPending}
          onClick={() => rebuild.mutate(undefined, { onError: () => toast("A rebuild is already running.") })}
        >
          {status?.rebuilding ? "Rebuilding…" : "Rebuild"}
        </Button>
      </Field>
    </Section>
  );
}
