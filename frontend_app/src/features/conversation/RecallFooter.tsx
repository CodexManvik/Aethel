import { useState } from "react";
import type { Recalled } from "../../stores/session";
import { showInConversation } from "./useConversations";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });

/** Under a reply: what Aethel remembered while writing it. */
export function RecallFooter({ recalled }: { recalled: Recalled }) {
  const [open, setOpen] = useState(false);
  const { facts, episodes } = recalled;
  if (!facts.length && !episodes.length) return null;
  const summary = [
    facts.length ? plural(facts.length, "fact", "facts") : "",
    episodes.length ? plural(episodes.length, "earlier moment", "earlier moments") : "",
  ].filter(Boolean).join(" · ");
  return (
    <div className="mt-2 font-sans text-[12.5px] text-muted">
      <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)} className="hover:text-ink">
        Recalled {summary}
      </button>
      {open && (
        <ul aria-label="Recalled" className="mt-1 space-y-1 border-l border-hairline pl-3 text-ink-2">
          {facts.map((f) => <li key={f.id}>{f.text}</li>)}
          {episodes.map((e) => (
            <li key={e.id}>
              <button type="button" className="text-left hover:text-ink" onClick={() => showInConversation(e.conversation_id)}>
                <span className="text-muted">{day(e.created_at)}: </span>
                {e.text.slice(0, 120)}{e.text.length > 120 ? "…" : ""}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
