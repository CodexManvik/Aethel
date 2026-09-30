import { useState, type KeyboardEvent } from "react";
import { toast } from "sonner";
import { showInConversation } from "../conversation/useConversations";
import { inputClass } from "../../ui/Field";
import { cn } from "../../ui/cn";
import { useAddFact, useDeleteFact, useEditFact, useFactHistory, type Fact, type FactEvent } from "./memoryApi";

const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });

const OP_LABEL: Record<FactEvent["op"], string> = { add: "remembered", update: "changed", delete: "forgotten" };

function describe(e: FactEvent): string {
  const who = e.actor === "user" ? "by you" : "by Aethel";
  if (e.op === "update") return `${OP_LABEL.update} ${who}: “${e.old_text}” → “${e.new_text}”`;
  return `${OP_LABEL[e.op]} ${who}: “${e.op === "add" ? e.new_text : e.old_text}”`;
}

export function FactCard({ fact }: { fact: Fact }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(fact.text);
  const [showHistory, setShowHistory] = useState(false);
  const edit = useEditFact();
  const remove = useDeleteFact();
  const add = useAddFact();
  const history = useFactHistory(fact.id, showHistory);

  const save = () => {
    const text = draft.trim();
    setEditing(false);
    if (text && text !== fact.text) edit.mutate({ id: fact.id, text });
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") save();
    if (e.key === "Escape") setEditing(false);
  };
  const onDelete = () =>
    remove.mutate(fact.id, {
      onSuccess: () =>
        toast("Fact deleted", {
          duration: 5000,
          action: { label: "Undo", onClick: () => add.mutate({ scope: fact.scope, text: fact.text }) },
        }),
    });

  const source = fact.conversation_id ? (
    <button
      type="button"
      className="underline decoration-hairline underline-offset-2 hover:text-ink"
      onClick={() => showInConversation(fact.conversation_id!, fact.source_message_id)}
    >
      from <span className="italic">{fact.conversation_title || "a conversation"}</span>, {day(fact.created_at)}
    </button>
  ) : fact.added_by === "user" ? (
    <span>added by you, {day(fact.created_at)}</span>
  ) : (
    <span>from a deleted conversation</span>
  );

  return (
    <article aria-label={fact.text} className="rounded-xl border border-hairline bg-paper px-4 py-3">
      {editing ? (
        <input
          autoFocus
          aria-label="Edit fact"
          value={draft}
          maxLength={300}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          onBlur={() => setEditing(false)}
          className={cn(inputClass, "w-full font-voice text-[16px]")}
        />
      ) : (
        <button
          type="button"
          title="Click to edit"
          onClick={() => { setDraft(fact.text); setEditing(true); }}
          className="w-full text-left font-voice text-[16px] leading-snug text-ink hover:text-ink-2"
        >
          {fact.text}
        </button>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-muted">
        {source}
        <button type="button" aria-expanded={showHistory} className="hover:text-ink" onClick={() => setShowHistory((v) => !v)}>
          History
        </button>
        <button type="button" className="hover:text-accent" onClick={onDelete}>
          Delete
        </button>
      </div>
      {showHistory && (
        <ul aria-label="History" className="mt-2 space-y-1 border-t border-hairline pt-2 text-[12.5px] text-ink-2">
          {(history.data ?? []).map((e) => (
            <li key={e.id}>
              <span className="text-muted">{day(e.created_at)} · </span>
              {describe(e)}
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
