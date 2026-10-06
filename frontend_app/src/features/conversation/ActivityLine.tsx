/** Under a reply that is still being written: what Aethel is doing on the web right now. */
export function ActivityLine({ text }: { text: string }) {
  return (
    <p role="status" className="mt-1.5 flex items-center gap-2 font-sans text-[12.5px] text-muted">
      <span aria-hidden className="inline-flex gap-1">
        <span className="breathe size-1 rounded-full bg-faint" />
        <span className="breathe size-1 rounded-full bg-faint [animation-delay:200ms]" />
        <span className="breathe size-1 rounded-full bg-faint [animation-delay:400ms]" />
      </span>
      {text}
    </p>
  );
}
