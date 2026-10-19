import { useEffect } from "react";
import { MotionConfig } from "motion/react";
import { Toaster } from "sonner";
import { Rail } from "./Rail";
import { useSessionEvents } from "./useSessionEvents";
import { resolveMotion, useUi, watchSystemAppearance } from "../../stores/ui";
import { ConversationView } from "../conversation/ConversationView";
import { ThreadsPanel } from "../conversation/ThreadsPanel";
import { SettingsView } from "../settings/SettingsView";
import { MemoryView } from "../memory/MemoryView";
import { TasksView } from "../history/TasksView";
import { CallView } from "../call/CallView";
import { PersonasView } from "../personas/PersonasView";
import { useActivePersona } from "../personas/personasApi";
import { applyPersonaAccent } from "../../lib/accent";

export function AppShell() {
  const { screen, motion, callOpen } = useUi();
  useSessionEvents();
  useEffect(() => watchSystemAppearance(), []);
  const accent = useActivePersona()?.accent ?? null;
  useEffect(() => applyPersonaAccent(accent), [accent?.light, accent?.dark]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <MotionConfig reducedMotion={resolveMotion(motion) === "reduced" ? "always" : "never"}>
      <div className="grain flex h-full bg-canvas text-ink">
        <Rail />
        <main className="relative min-w-0 flex-1">
          <ThreadsPanel />
          {screen === "conversation" ? <ConversationView /> : screen === "memory" ? <MemoryView />
            : screen === "tasks" ? <TasksView /> : screen === "personas" ? <PersonasView /> : <SettingsView />}
          {callOpen && <CallView />}
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
