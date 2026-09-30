import { useState } from "react";
import { motion } from "motion/react";
import { toast } from "sonner";
import { api } from "../../lib/api";
import type { FactChange } from "../../lib/events.gen";
import { useSession } from "../../stores/session";
import { cn } from "../../ui/cn";

const LABEL = { add: "Noted", update: "Updated", delete: "Forgot" } as const;

const what = (c: FactChange) => (c.op === "delete" ? c.old_text : c.text) ?? "";

/** Under a user message: what Aethel remembered from it. Memory is never silent, and every change can be undone. */
export function NotedLine({ messageId, changes }: { messageId: string; changes: FactChange[] }) {
  const [busy, setBusy] = useState<number | null>(null);
  const undo = async (index: number) => {
    setBusy(index);
    try {
      const updated = await api<FactChange[]>("/api/memory/facts/undo", {
        method: "POST",
        body: JSON.stringify({ message_id: messageId, index }),
      });
      useSession.getState().setNoted(messageId, updated);
    } catch {
      toast("Couldn't undo that.");
    } finally {
      setBusy(null);
    }
  };
  return (
    <motion.ul
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 260, damping: 30 }}
      aria-label="Remembered from this message"
      className="mt-1 self-end space-y-0.5 text-right font-sans text-[12.5px] text-muted"
    >
      {changes.map((c, i) => (
        <li key={`${c.fact_id}-${i}`} className="flex items-center justify-end gap-2">
          <span className={cn(c.undone && "line-through opacity-60")}>
            {LABEL[c.op]}: {what(c)}
          </span>
          {c.undone ? (
            <span className="text-faint">undone</span>
          ) : (
            <button
              type="button"
              aria-label={`Undo: ${LABEL[c.op]} ${what(c)}`}
              disabled={busy !== null}
              onClick={() => undo(i)}
              className="underline decoration-hairline underline-offset-2 hover:text-ink disabled:opacity-40"
            >
              Undo
            </button>
          )}
        </li>
      ))}
    </motion.ul>
  );
}
