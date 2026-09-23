import { useEffect } from "react";
import { MotionConfig } from "motion/react";
import { Toaster } from "sonner";
import { Rail } from "./Rail";
import { useSessionEvents } from "./useSessionEvents";
import { resolveMotion, useUi, watchSystemAppearance } from "../../stores/ui";
import { ConversationView } from "../conversation/ConversationView";
import { SettingsView } from "../settings/SettingsView";

export function AppShell() {
  const { screen, motion } = useUi();
  useSessionEvents();
  useEffect(() => watchSystemAppearance(), []);

  return (
    <MotionConfig reducedMotion={resolveMotion(motion) === "reduced" ? "always" : "never"}>
      <div className="grain flex h-full bg-canvas text-ink">
        <Rail />
        <main className="relative min-w-0 flex-1">
          {screen === "conversation" ? <ConversationView /> : <SettingsView />}
        </main>
        <Toaster
          position="bottom-right"
          toastOptions={{
            className: "!bg-paper !text-ink !border-hairline !font-sans !text-[13px]",
          }}
        />
      </div>
    </MotionConfig>
  );
}
