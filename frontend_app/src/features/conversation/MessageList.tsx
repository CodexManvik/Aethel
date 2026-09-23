import { useLayoutEffect, useRef } from "react";
import { motion } from "motion/react";
import { useSession } from "../../stores/session";
import { PersonaMessage } from "./PersonaMessage";
import { UserMessage } from "./UserMessage";

export function MessageList() {
  const messages = useSession((s) => s.messages);
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTo({ top: el.scrollHeight });
  }, [messages]);

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
            className="flex flex-col"
          >
            {m.role === "user" ? <UserMessage message={m} /> : <PersonaMessage message={m} />}
          </motion.div>
        ))}
      </div>
    </div>
  );
}
