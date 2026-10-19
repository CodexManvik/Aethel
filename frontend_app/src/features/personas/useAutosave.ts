import { useCallback, useEffect, useRef, useState } from "react";

export type SaveState = "idle" | "saving" | "saved" | "error";

/** `newer` over `older`; sections are merged too (the server merges them), so two edited sections both survive. */
function merge<T extends object>(older: Partial<T>, newer: Partial<T>): Partial<T> {
  const out: Record<string, unknown> = { ...older };
  for (const [k, v] of Object.entries(newer)) {
    const before = out[k];
    out[k] = k === "sections" && before && typeof before === "object" ? { ...before, ...(v as object) } : v;
  }
  return out as Partial<T>;
}

/** Collects changes and saves them together once editing pauses for `delay` ms; whatever is pending is saved when
 * the editor closes. */
export function useAutosave<T extends object>(save: (patch: Partial<T>) => Promise<unknown>, delay = 800) {
  const pending = useRef<Partial<T>>({});
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveRef = useRef(save);
  saveRef.current = save;
  const [state, setState] = useState<SaveState>("idle");

  const flush = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const patch = pending.current;
    pending.current = {};
    if (Object.keys(patch).length === 0) return;
    setState("saving");
    try {
      await saveRef.current(patch);
      setState("saved");
    } catch {
      // kept for the next try (the next edit, or closing the editor); anything typed since wins over it
      pending.current = merge(patch, pending.current);
      setState("error");
    }
  }, []);

  const queue = useCallback((patch: Partial<T>) => {
    pending.current = merge(pending.current, patch);
    setState("idle");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void flush(), delay);
  }, [delay, flush]);

  /** Drops whatever is pending (the persona is being deleted). */
  const cancel = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    pending.current = {};
  }, []);

  useEffect(() => () => void flush(), [flush]);
  return { queue, flush, cancel, state };
}
