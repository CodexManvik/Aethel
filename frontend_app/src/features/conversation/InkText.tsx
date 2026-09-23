/** Streaming text: every word is its own span so new words "settle" in. Keys
 * are positional, so a word that grows token by token keeps its span. */
export function InkText({ text }: { text: string }) {
  const parts = text.split(/(\s+)/);
  return (
    <p className="whitespace-pre-wrap">
      {parts.map((part, i) =>
        part === "" || /^\s+$/.test(part) ? part : (
          <span key={i} className="ink-word">
            {part}
          </span>
        ),
      )}
    </p>
  );
}
