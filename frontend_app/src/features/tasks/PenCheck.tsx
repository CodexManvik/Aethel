/** A plan-step marker: an empty ring, a slow terracotta arc while active, and a
 * pen-stroke tick once done. */
export function PenCheck({ done, active }: { done: boolean; active: boolean }) {
  return (
    <svg viewBox="0 0 20 20" className="mt-0.5 size-4 shrink-0" aria-hidden>
      <circle cx="10" cy="10" r="8.5" fill="none" strokeWidth="1.3"
        className={done ? "stroke-ink" : "stroke-hairline"} />
      {active && !done && (
        <circle cx="10" cy="10" r="8.5" fill="none" strokeWidth="1.5" strokeDasharray="12 42"
          strokeLinecap="round" className="slow-arc stroke-accent" />
      )}
      {done && (
        <path d="M6 10.5l2.6 2.6L14 7.4" fill="none" strokeWidth="1.6" strokeLinecap="round"
          strokeLinejoin="round" className="pen-stroke stroke-ink" />
      )}
    </svg>
  );
}
