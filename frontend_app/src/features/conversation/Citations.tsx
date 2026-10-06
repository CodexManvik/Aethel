import type { Source } from "../../lib/events.gen";

// Only a plain http(s) address is ever made a link: a source is data from the web, so it isn't trusted to be one.
const isWebAddress = (url: string) => /^https?:\/\/[^\s<>"]+$/i.test(url);

export const siteOf = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
};

/** Turns [n] in a reply into a link to source n, but only for numbers that are real sources: Aethel is told
 * never to invent one, and a stray "[9]" stays plain text. Code is left alone, and so is a [n] that already
 * has its own link. The "cite" title marks the links PersonaMessage draws as footnotes. */
export function citeText(content: string, sources: Source[]): string {
  if (!sources.length) return content;
  const urls = new Map(sources.filter((s) => isWebAddress(s.url)).map((s) => [s.n, s.url]));
  return content
    .split(/(```[\s\S]*?```|`[^`\n]*`)/) // odd parts are code
    .map((part, i) =>
      i % 2 ? part : part.replace(/\[(\d{1,3})\](?!\()/g, (m, n) => (urls.has(Number(n)) ? `[${n}](<${urls.get(Number(n))}> "cite")` : m)),
    )
    .join("");
}

/** Under a finished reply: where its footnotes came from. */
export function SourceList({ sources }: { sources: Source[] }) {
  if (!sources.length) return null;
  return (
    <ol aria-label="Sources" className="mt-2 list-none space-y-0.5 font-sans text-[12.5px] text-muted">
      {sources.map((s) => (
        <li key={s.n}>
          {`${s.n}. `}
          {isWebAddress(s.url) ? (
            <a href={s.url} target="_blank" rel="noreferrer" className="text-ink-2 underline decoration-hairline underline-offset-2 hover:text-ink">
              {s.title}
            </a>
          ) : (
            s.title
          )}
          {isWebAddress(s.url) ? ` · ${siteOf(s.url)}` : ""}
        </li>
      ))}
    </ol>
  );
}
