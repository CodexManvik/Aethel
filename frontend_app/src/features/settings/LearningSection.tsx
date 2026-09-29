import { Field } from "../../ui/Field";
import { Switch } from "../../ui/Switch";
import type { AppSettings } from "../../lib/types";
import { Section } from "./Section";
import { useUpdateSettings } from "./useSettings";

export function LearningSection({ settings }: { settings: AppSettings }) {
  const update = useUpdateSettings();
  const s1 = settings.system1;
  return (
    <Section title="Learning" description="Aethel keeps notes on your apps and writes down how it did each task, so the next one goes faster.">
      <Field label="System 1" hint="A small local model (runs on the CPU) for quick judgments: routing messages, picking skills, checking results.">
        <Switch label="System 1" checked={s1.enabled} onCheckedChange={(v) => update.mutate({ system1: { enabled: v } })} />
      </Field>
      <Field label="Start tasks from messages" hint="When a message clearly asks for something to be done, start a task without the Task pill. Saying “stop” stops a running task.">
        <Switch label="Start tasks from messages" checked={s1.auto_tasks} disabled={!s1.enabled}
          onCheckedChange={(v) => update.mutate({ system1: { auto_tasks: v } })} />
      </Field>
      <Field label="Replay pictures" hint="A small screenshot after each on-screen step, for the replay in Tasks. Kept only on this PC.">
        <Switch label="Replay pictures" checked={settings.replay_thumbnails}
          onCheckedChange={(v) => update.mutate({ replay_thumbnails: v })} />
      </Field>
      <Field label="Use new skills straight away" hint="Off: new skills wait in Memory until you approve them.">
        <Switch label="Use new skills straight away" checked={settings.auto_approve_skills}
          onCheckedChange={(v) => update.mutate({ auto_approve_skills: v })} />
      </Field>
    </Section>
  );
}
