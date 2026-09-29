import { useSettings } from "./useSettings";
import { ModeSection } from "./ModeSection";
import { ProvidersSection } from "./ProvidersSection";
import { RolesSection } from "./RolesSection";
import { LocalModelSection } from "./LocalModelSection";
import { AppearanceSection } from "./AppearanceSection";
import { ToolsSection } from "./ToolsSection";
import { LearningSection } from "./LearningSection";

export function SettingsView() {
  const { data: settings, error } = useSettings();
  return (
    <div className="h-full overflow-y-auto bg-paper">
      <div className="mx-auto max-w-3xl px-10 py-10">
        <h1 className="font-display text-[44px] leading-none">Settings</h1>
        {error && <p role="alert" className="mt-4 text-accent">{String(error)}</p>}
        {settings && (
          <>
            <ModeSection settings={settings} />
            <ProvidersSection settings={settings} />
            <RolesSection settings={settings} />
            <LocalModelSection settings={settings} />
            <ToolsSection />
            <LearningSection settings={settings} />
            <AppearanceSection />
          </>
        )}
      </div>
    </div>
  );
}
