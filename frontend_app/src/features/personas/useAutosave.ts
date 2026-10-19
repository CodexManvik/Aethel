import { useCallback, useEffect, useRef, useState } from "react";

export type SaveState = "idle" | "saving" | "saved" | "error";

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
      setState("error");
    }
  }, []);

  const queue = useCallback((patch: Partial<T>) => {
    const merged: Record<string, unknown> = { ...pending.current };
    for (const [k, v] of Object.entries(patch)) {
      const before = merged[k];
      // sections are merged on the server too, so two edited sections both survive one save
      merged[k] = k === "sections" && before && typeof before === "object" ? { ...before, ...(v as object) } : v;
    }
    pending.current = merged as Partial<T>;
    setState("idle");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void flush(), delay);
  }, [delay, flush]);

  useEffect(() => () => void flush(), [flush]);
  return { queue, flush, state };
}
