import { useEffect, useState, type ReactNode } from "react";
import { waitForBackend } from "../../lib/api";
import { pushStoredKeys } from "../../lib/keys";

type State = { phase: "waiting" } | { phase: "ready" } | { phase: "failed"; message: string };

export function Boot({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ phase: "waiting" });

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        await waitForBackend();
        await pushStoredKeys();
        if (alive) setState({ phase: "ready" });
      } catch (err) {
        if (alive) setState({ phase: "failed", message: err instanceof Error ? err.message : String(err) });
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (state.phase === "ready") return <>{children}</>;
  return (
    <div className="grain flex h-full flex-col items-center justify-center gap-4 bg-canvas text-ink">
      <h1 className="font-display text-6xl leading-none">Aethel</h1>
      {state.phase === "waiting" ? (
        <p className="flex items-center gap-2 text-[13px] text-muted">
          <span className="breathe inline-block size-1.5 rounded-full bg-accent" />
          waking up…
        </p>
      ) : (
        <p role="alert" className="max-w-md text-center font-voice text-[16px] text-muted">
          {state.message}
        </p>
      )}
    </div>
  );
}
