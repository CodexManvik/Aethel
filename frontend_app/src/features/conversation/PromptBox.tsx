import { useLayoutEffect, useRef, useState } from "react";
import { ArrowUp, ListChecks, Square } from "lucide-react";
import { cn } from "../../ui/cn";

interface Props {
  onSend: (text: string, mode: "chat" | "task") => void;
  onStop: () => void;
  streaming: boolean;
  placeholder?: string;
}

export function PromptBox({ onSend, onStop, streaming, placeholder = "Say something to Aethel…" }: Props) {
  const [text, setText] = useState("");
  const [mode, setMode] = useState<"chat" | "task">("chat");
  const ref = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [text]);

  const submit = () => {
    if (!text.trim() || streaming) return;
    onSend(text, mode);
    setText("");
    setMode("chat");
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="mx-auto mb-6 flex w-full max-w-3xl items-end gap-3 rounded-[14px] border border-hairline bg-paper px-4 py-3 transition-colors duration-[var(--dur-fast)] focus-within:border-muted"
    >
      <button
        type="button"
        aria-pressed={mode === "task"}
        title="Give Aethel a task to carry out"
        onClick={() => setMode((m) => (m === "task" ? "chat" : "task"))}
        className={cn(
          "mb-0.5 flex h-7 items-center gap-1 rounded-full border px-2.5 text-[11.5px] transition-colors",
          mode === "task" ? "border-accent/50 bg-accent-soft text-accent" : "border-hairline text-muted hover:text-ink",
        )}
      >
        <ListChecks size={13} /> Task
      </button>
      <textarea
        ref={ref}
        aria-label="Message"
        rows={1}
        value={text}
        placeholder={mode === "task" ? "Describe a task for Aethel…" : placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
        className="max-h-[220px] flex-1 resize-none bg-transparent py-1 text-[14.5px] leading-6 text-ink outline-none placeholder:text-faint"
      />
      {streaming ? (
        <button type="button" aria-label="Stop" onClick={onStop} className="grid size-8 place-items-center rounded-full border border-hairline text-ink hover:border-muted">
          <Square size={12} fill="currentColor" />
        </button>
      ) : (
        <button type="submit" aria-label="Send" disabled={!text.trim()} className="grid size-8 place-items-center rounded-full bg-ink text-canvas transition-opacity disabled:opacity-25">
          <ArrowUp size={16} strokeWidth={2} />
        </button>
      )}
    </form>
  );
}
