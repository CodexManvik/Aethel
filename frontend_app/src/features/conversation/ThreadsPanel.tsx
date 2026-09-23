import { AnimatePresence, motion } from "motion/react";
import { Plus, Trash2 } from "lucide-react";
import { useUi } from "../../stores/ui";
import { useSession } from "../../stores/session";
import { cn } from "../../ui/cn";
import { formatRelative } from "./greeting";
import { openConversation, useConversations, useDeleteConversation } from "./useConversations";

export function ThreadsPanel() {
  const { threadsOpen, setThreadsOpen } = useUi();
  const activeId = useSession((s) => s.conversationId);
  const { data: conversations = [] } = useConversations();
  const remove = useDeleteConversation();

  return (
    <AnimatePresence>
      {threadsOpen && (
        <motion.aside
          aria-label="Conversations"
          initial={{ opacity: 0, x: -16 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: -16 }}
          transition={{ type: "spring", stiffness: 260, damping: 30 }}
          className="absolute inset-y-0 left-0 z-10 flex w-[300px] flex-col border-r border-hairline bg-canvas shadow-lift"
        >
          <div className="flex items-center justify-between px-5 pt-6 pb-3">
            <h2 className="font-display text-[24px] leading-none">Conversations</h2>
            <button
              type="button"
              aria-label="Start a new conversation"
              onClick={() => {
                useSession.getState().setConversation(null, []);
                setThreadsOpen(false);
              }}
              className="grid size-8 place-items-center rounded-full border border-hairline text-muted hover:text-ink"
            >
              <Plus size={15} />
            </button>
          </div>
          <ul className="flex-1 overflow-y-auto px-2 pb-4">
            {conversations.length === 0 && <li className="px-3 py-6 font-voice text-[15px] italic text-muted">Nothing here yet.</li>}
            {conversations.map((c) => (
              <li key={c.id} className="group relative">
                <button
                  type="button"
                  onClick={async () => {
                    await openConversation(c.id);
                    setThreadsOpen(false);
                  }}
                  className={cn(
                    "w-full rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-paper",
                    c.id === activeId && "bg-well",
                  )}
                >
                  <span className="block truncate pr-7 text-[13.5px] text-ink">{c.title || "Untitled"}</span>
                  <span className="text-[11.5px] text-faint">{formatRelative(c.updated_at)}</span>
                </button>
                <button
                  type="button"
                  aria-label={`Delete ${c.title || "conversation"}`}
                  onClick={() => remove.mutate(c.id)}
                  className="absolute top-3 right-2 hidden text-faint hover:text-accent group-hover:block"
                >
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
