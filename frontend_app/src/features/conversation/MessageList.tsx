import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { useSession } from "../../stores/session";
import { useUi } from "../../stores/ui";
import { cn } from "../../ui/cn";
import { PersonaMessage } from "./PersonaMessage";
import { UserMessage } from "./UserMessage";

export function MessageList() {
  const messages = useSession((s) => s.messages);
  const focusId = useUi((s) => s.focusMessageId);
  const [highlight, setHighlight] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current && !focusId) el.scrollTo({ top: el.scrollHeight });
  }, [messages, focusId]);

  // Jump to a message opened from Memory (a fact's source): scroll it to the middle and glow briefly.
  // (The conversation is loaded before focusMessageId is set, so a missing row means the message is gone.)
  useEffect(() => {
    if (!focusId) return;
    const row = ref.current?.querySelector<HTMLElement>(`[data-message-id="${CSS.escape(focusId)}"]`);
    useUi.getState().setFocusMessage(null);
    if (!row) return;
    stick.current = false;
    row.scrollIntoView?.({ block: "center" });
    setHighlight(focusId);
  }, [focusId, messages]);

  useEffect(() => {
    if (!highlight) return;
    const t = setTimeout(() => setHighlight(null), 2000);
    return () => clearTimeout(t);
  }, [highlight]);

  return (
    <div
      ref={ref}
      role="log"
      aria-live="polite"
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
      }}
      className="flex-1 overflow-y-auto"
    >
      <div className="mx-auto flex max-w-3xl flex-col gap-5 px-10 py-8">
        {messages.map((m) => (
          <motion.div
            key={m.id}
            layout="position"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ type: "spring", stiffness: 260, damping: 30 }}
            data-message-id={m.id}
            className={cn("flex flex-col rounded-xl transition-shadow", highlight === m.id && "ring-1 ring-accent/40")}
          >
            {m.role === "user" ? <UserMessage message={m} /> : <PersonaMessage message={m} />}
          </motion.div>
        ))}
      </div>
    </div>
  );
}
