import { useUi, type MotionPref, type ThemePref } from "../../stores/ui";
import { Field } from "../../ui/Field";
import { cn } from "../../ui/cn";
import { Section } from "./Section";

function Segmented<T extends string>({ label, value, options, onChange }: {
  label: string; value: T; options: { value: T; label: string }[]; onChange: (v: T) => void;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex rounded-full border border-hairline bg-paper p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn("rounded-full px-3 py-1 text-[12.5px] text-muted transition-colors", value === o.value && "bg-ink text-canvas")}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function AppearanceSection() {
  const { theme, motion, setTheme, setMotion } = useUi();
  return (
    <Section title="Appearance">
      <Field label="Theme">
        <Segmented<ThemePref> label="Theme" value={theme} onChange={setTheme} options={[
          { value: "light", label: "Paper" }, { value: "dark", label: "Ink" }, { value: "system", label: "System" },
        ]} />
      </Field>
      <Field label="Motion" hint="Reduced keeps fades but removes movement.">
        <Segmented<MotionPref> label="Motion" value={motion} onChange={setMotion} options={[
          { value: "system", label: "System" }, { value: "full", label: "Full" }, { value: "reduced", label: "Reduced" },
        ]} />
      </Field>
    </Section>
  );
}
