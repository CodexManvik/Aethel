import { useEffect, useState, type ReactNode } from "react";
import { toast } from "sonner";
import { waitForBackend } from "../../lib/api";
import { backendExited, backendLogTail, inTauri, openBackendLogs, restartBackend, setupLogTail, setupStatus,
  type SetupStatus } from "../../lib/backend";
import { pushStoredKeys } from "../../lib/keys";
import { Button } from "../../ui/Button";

type State = { phase: "waiting" } | { phase: "setup"; status: SetupStatus } | { phase: "ready" }
  | { phase: "failed"; message: string; log: string | null };

class SetupFailed extends Error {}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** The installed app sets up its own Python and packages on first run (minutes, once): wait for it, showing each
 * step. Throws SetupFailed with uv's error. A dev build is ready at once. */
async function waitForSetup(alive: () => boolean, show: (s: SetupStatus) => void): Promise<void> {
  while (alive()) {
    const status = await setupStatus();
    if (status.phase === "ready") return;
    if (status.phase === "failed") throw new SetupFailed(status.error ?? "Aethel couldn't finish setting itself up.");
    show(status);
    await sleep(700);
  }
}

/** Rejects as soon as the backend process is known to have died, instead of waiting out the timeout. */
async function watchForExit(alive: () => boolean): Promise<never> {
  while (alive()) {
    const why = await backendExited().catch(() => null);
    if (why) throw new Error(`The Aethel backend didn't start: ${why}.`);
    await sleep(1000);
  }
  return new Promise<never>(() => {});
}

export function Boot({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ phase: "waiting" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setState({ phase: "waiting" });
    (async () => {
      try {
        await waitForSetup(() => alive, (status) => alive && setState({ phase: "setup", status }));
        if (alive) setState({ phase: "waiting" });
        await Promise.race([waitForBackend(), watchForExit(() => alive)]);
        await pushStoredKeys();
        if (alive) setState({ phase: "ready" });
      } catch (err) {
        const log = await (err instanceof SetupFailed ? setupLogTail() : backendLogTail()).catch(() => null);
        if (alive) setState({ phase: "failed", message: err instanceof Error ? err.message : String(err), log });
      }
    })();
    return () => {
      alive = false;
    };
  }, [attempt]);

  if (state.phase === "ready") return <>{children}</>;
  return (
    <div className="grain flex h-full flex-col items-center justify-center gap-4 bg-canvas px-8 text-ink">
      <h1 className="font-display text-6xl leading-none">Aethel</h1>
      {state.phase === "waiting" ? (
        <p className="flex items-center gap-2 text-[13px] text-muted">
          <span className="breathe inline-block size-1.5 rounded-full bg-accent" />
          waking up…
        </p>
      ) : state.phase === "setup" ? (
        <div className="flex max-w-md flex-col items-center gap-3 text-center">
          <p className="font-voice text-[16px] italic text-muted">Setting up for the first time. This needs the internet once.</p>
          <p className="flex items-center gap-2 text-[13px] text-ink-2" aria-live="polite">
            <span className="breathe inline-block size-1.5 rounded-full bg-accent" />
            {state.status.step || "Checking what's there…"}
            {state.status.total > 0 && <span className="text-faint">({state.status.done + 1} of {state.status.total})</span>}
          </p>
        </div>
      ) : (
        <>
          <p role="alert" className="max-w-xl text-center font-voice text-[16px] text-muted">{state.message}</p>
          {state.log && (
            <pre
              aria-label="End of the log"
              tabIndex={0}
              className="max-h-[45vh] w-full max-w-4xl overflow-auto whitespace-pre-wrap rounded-lg border border-hairline bg-paper p-4 font-mono text-[11.5px] leading-5 text-ink-2"
            >
              {state.log}
            </pre>
          )}
          <div className="flex flex-wrap justify-center gap-2">
            <Button variant="primary" onClick={() => void restartBackend().then(() => setAttempt((n) => n + 1)).catch((e) => toast(String(e)))}>
              Try again
            </Button>
            {inTauri() && (
              <>
                <Button onClick={() => void openBackendLogs().catch((e) => toast(String(e)))}>Open log folder</Button>
                {state.log && (
                  <Button onClick={() => void navigator.clipboard.writeText(state.log ?? "").then(() => toast("Log copied."))}>
                    Copy log
                  </Button>
                )}
              </>
            )}
          </div>
          {!inTauri() && (
            <p className="text-[12.5px] text-muted">Running in a browser: the backend's output is in the terminal that started it.</p>
          )}
        </>
      )}
    </div>
  );
}
