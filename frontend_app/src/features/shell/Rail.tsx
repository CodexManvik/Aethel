import { BookMarked, MessagesSquare, Moon, PenLine, Settings2, Sun } from "lucide-react";
import { IconButton } from "../../ui/IconButton";
import { resolveTheme, useUi } from "../../stores/ui";
import { useSession } from "../../stores/session";
import { cn } from "../../ui/cn";

export function Rail() {
  const { screen, threadsOpen, theme, setScreen, toggleThreads, setTheme } = useUi();
  const dark = resolveTheme(theme) === "dark";

  return (
    <nav className="relative z-20 flex h-full w-16 flex-col items-center gap-3 border-r border-hairline bg-canvas py-5">
      <button
        type="button"
        aria-label="Aethel"
        title="Aethel"
        onClick={() => setScreen("conversation")}
        className={cn(
          "grid size-9 place-items-center rounded-full bg-paper font-display text-[19px] leading-none text-ink transition-shadow duration-[var(--dur-base)]",
          screen === "conversation"
            ? "shadow-[0_0_0_2px_var(--canvas),0_0_0_3.5px_var(--accent)]"
            : "shadow-[0_0_0_1px_var(--hairline)]",
        )}
      >
        Æ
      </button>
      <div className="flex-1" />
      <IconButton label="New conversation" onClick={() => { useSession.getState().setConversation(null, []); setScreen("conversation"); }}>
        <PenLine size={17} strokeWidth={1.5} />
      </IconButton>
      <IconButton label="Conversations" active={threadsOpen} onClick={toggleThreads}>
        <MessagesSquare size={17} strokeWidth={1.5} />
      </IconButton>
      <IconButton label={dark ? "Switch to light theme" : "Switch to dark theme"} onClick={() => setTheme(dark ? "light" : "dark")}>
        {dark ? <Sun size={17} strokeWidth={1.5} /> : <Moon size={17} strokeWidth={1.5} />}
      </IconButton>
      <IconButton label="Memory" active={screen === "memory"} onClick={() => setScreen("memory")}>
        <BookMarked size={17} strokeWidth={1.5} />
      </IconButton>
      <IconButton label="Settings" active={screen === "settings"} onClick={() => setScreen("settings")}>
        <Settings2 size={17} strokeWidth={1.5} />
      </IconButton>
    </nav>
  );
}
