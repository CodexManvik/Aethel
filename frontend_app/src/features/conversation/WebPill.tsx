import { Globe } from "lucide-react";
import { useSession } from "../../stores/session";
import { useUi } from "../../stores/ui";
import { cn } from "../../ui/cn";
import { useSettings } from "../settings/useSettings";
import { useConversations, useSetWeb } from "./useConversations";

/** By the message box: may Aethel use the web in this conversation? On, off, or follow Settings. A click goes
 * follow Settings → on → off → follow Settings. Private mode keeps it off and locked. */
export function WebPill() {
  const conversationId = useSession((s) => s.conversationId);
  const newChatWeb = useUi((s) => s.newChatWeb);
  const { data: conversations } = useConversations();
  const { data: settings } = useSettings();
  const setWeb = useSetWeb();
  const here = Array.isArray(conversations) ? conversations.find((c) => c.id === conversationId) : undefined;
  const choice: boolean | null = conversationId ? (here?.web ?? null) : newChatWeb;
  const priv = settings?.private_mode ?? false;
  const on = !priv && (choice ?? settings?.internet ?? false);
  const label = priv
    ? "Web: off in private mode"
    : choice === null
      ? `Web: follows Settings (${on ? "on" : "off"})`
      : choice ? "Web: on" : "Web: off";
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={on}
      disabled={priv}
      title={priv ? "Private mode keeps everything on this computer"
        : "Let Aethel search the web and cite sources. Click to switch between on, off and following Settings."}
      onClick={() => void setWeb(choice === null ? true : choice ? false : null)}
      className={cn(
        "mb-0.5 flex h-7 items-center gap-1 rounded-full border px-2.5 text-[11.5px] transition-colors disabled:opacity-50",
        on ? "border-accent/50 bg-accent-soft text-accent" : "border-hairline text-muted hover:text-ink",
        choice === null && !priv && "border-dashed",
      )}
    >
      <Globe size={13} /> Web{choice === null && !priv ? " · auto" : ""}
    </button>
  );
}
