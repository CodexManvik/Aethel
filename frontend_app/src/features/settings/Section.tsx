import type { ReactNode } from "react";

export function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <section className="border-t border-hairline py-8 first:border-t-0">
      <h2 className="font-display text-[26px] leading-none text-ink">{title}</h2>
      {description && <p className="mt-2 max-w-xl font-voice text-[15px] text-muted">{description}</p>}
      <div className="mt-4 divide-y divide-hairline">{children}</div>
    </section>
  );
}
