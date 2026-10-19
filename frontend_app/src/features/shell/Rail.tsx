import { BookMarked, History, MessagesSquare, Moon, PenLine, Settings2, Sun, UsersRound } from "lucide-react";
import { IconButton } from "../../ui/IconButton";
import { resolveTheme, useUi } from "../../stores/ui";
import { useSession } from "../../stores/session";
import { glowOpacity, useTone } from "../../stores/tone";
import { PersonaAvatar } from "../personas/PersonaAvatar";
import { BUILTIN_ID, talkTo, useActivePersonaId, useOpenConversation, usePersonas, type Persona } from "../personas/personasApi";

const FALLBACK: Persona[] = [{ id: BUILTIN_ID, name: "Aethel", built_in: true, anchors: [], accent: null } as unknown as Persona];

export function Rail() {
  const { screen, threadsOpen, theme, setScreen, toggleThreads, setTheme, openStudio } = useUi();
  const dark = resolveTheme(theme) === "dark";
  const { data } = usePersonas();
  const personas = Array.isArray(data) && data.length ? data : FALLBACK;
  const activeId = useActivePersonaId();
  const conversationId = useSession((s) => s.conversationId);
  const open = useOpenConversation();
  // live from tone_changed; after a reload, what the conversation last stored
  const tone = useTone((s) => (conversationId ? s.byConversation[conversationId] : undefined))
    ?? (open?.tone ? { ...open.tone, label: "" } : undefined);

  return (
    <nav className="relative z-20 flex h-full w-16 flex-col items-center gap-3 border-r border-hairline bg-canvas py-5">
      <div className="flex flex-col items-center gap-2.5" aria-label="Personas" role="group">
        {personas.map((p) => {
          const active = p.id === activeId && screen === "conversation";
          return (
            <button key={p.id} type="button" aria-label={p.name} title={p.built_in ? p.name : `Talk to ${p.name}`}
              aria-current={active ? "true" : undefined}
              onClick={() => (p.id === activeId ? setScreen("conversation") : talkTo(p.id))}>
              <PersonaAvatar persona={p} active={active} glow={p.id === activeId && !p.built_in ? glowOpacity(tone) : 0} />
            </button>
          );
        })}
      </div>
      <div className="flex-1" />
      <IconButton label="New conversation" onClick={() => talkTo(activeId)}>
        <PenLine size={17} strokeWidth={1.5} />
      </IconButton>
      <IconButton label="Conversations" active={threadsOpen} onClick={toggleThreads}>
        <MessagesSquare size={17} strokeWidth={1.5} />
      </IconButton>
      <IconButton label={dark ? "Switch to light theme" : "Switch to dark theme"} onClick={() => setTheme(dark ? "light" : "dark")}>
        {dark ? <Sun size={17} strokeWidth={1.5} /> : <Moon size={17} strokeWidth={1.5} />}
      </IconButton>
      <IconButton label="Personas" active={screen === "personas"} onClick={() => openStudio(null)}>
        <UsersRound size={17} strokeWidth={1.5} />
      </IconButton>
      <IconButton label="Tasks" active={screen === "tasks"} onClick={() => setScreen("tasks")}>
        <History size={17} strokeWidth={1.5} />
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
