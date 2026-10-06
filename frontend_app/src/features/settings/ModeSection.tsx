import { Field } from "../../ui/Field";
import { Switch } from "../../ui/Switch";
import type { AppSettings } from "../../lib/types";
import { Section } from "./Section";
import { useUpdateSettings } from "./useSettings";

export function ModeSection({ settings }: { settings: AppSettings }) {
  const update = useUpdateSettings();
  return (
    <Section title="Mode" description="Hybrid uses your cloud keys for thinking; voice, memory and skills always stay on this computer.">
      <Field label="Private mode" hint="Everything runs on local models. Nothing leaves this computer.">
        <Switch label="Private mode" checked={settings.private_mode} onCheckedChange={(v) => update.mutate({ private_mode: v })} />
      </Field>
      <Field
        label="Allow web access"
        hint={settings.private_mode
          ? "Private mode keeps everything on this computer, so the web is off."
          : "Aethel can search the web and read pages, and cites its sources. Each conversation can override this with the Web button by the message box."}
      >
        <Switch label="Allow web access" checked={settings.internet && !settings.private_mode} disabled={settings.private_mode}
          onCheckedChange={(v) => update.mutate({ internet: v })} />
      </Field>
    </Section>
  );
}
