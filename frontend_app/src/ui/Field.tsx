import type { ReactNode } from "react";

interface Props {
  label: string;
  hint?: string;
  htmlFor?: string;
  children: ReactNode;
}

export function Field({ label, hint, htmlFor, children }: Props) {
  return (
    <div className="flex items-start justify-between gap-6 py-4">
      <div className="min-w-0">
        <label htmlFor={htmlFor} className="text-[13.5px] font-medium text-ink">
          {label}
        </label>
        {hint && <p className="mt-0.5 text-[12.5px] leading-5 text-muted">{hint}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

export const inputClass =
  "h-9 w-72 rounded-lg border border-hairline bg-paper px-3 text-[13.5px] text-ink placeholder:text-faint outline-none focus:border-muted";
