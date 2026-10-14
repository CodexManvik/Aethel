import { useEffect, useState } from "react";
import { motion, useReducedMotion, useSpring } from "motion/react";
import { MessageSquare, Mic, MicOff, PhoneOff } from "lucide-react";
import { useUi } from "../../stores/ui";
import { TERMINAL_STATES, useTasks } from "../../stores/tasks";
import { useVoice, type CallState } from "../../stores/voice";
import { InkText } from "../conversation/InkText";

const LIVE: CallState[] = ["connecting", "listening", "hearing", "thinking", "speaking"];

export function stateLine(state: CallState, muted: boolean): string {
  if (state === "connecting") return "Connecting…";
  if (muted && LIVE.includes(state)) return "Muted";
  if (state === "listening" || state === "hearing") return "Listening…";
  if (state === "thinking") return "Thinking…";
  if (state === "ended") return "Call ended";
  if (state === "error") return "The call stopped";
  return "";
}

/** The level that drives the rings: Aethel's voice while it speaks, half the microphone's while the user does. */
function useLevel(state: CallState): number {
  const [level, setLevel] = useState(0);
  useEffect(() => {
    let frame = 0;
    const tick = () => {
      const v = useVoice.getState();
      setLevel(state === "speaking" ? v.outputLevel() : state === "hearing" ? v.inputLevel * 0.5 : 0);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [state]);
  return Math.min(1, level * 4); // speech RMS is small; this maps a normal voice to most of the range
}

export function Portrait({ name, level }: { name: string; level: number }) {
  const reduced = useReducedMotion();
  const scale = useSpring(1, { stiffness: 140, damping: 22 });
  useEffect(() => { scale.set(1 + 0.08 * level); }, [level, scale]);
  return (
    <div className="relative grid size-[220px] place-items-center" data-testid="portrait" data-level={level.toFixed(2)}>
      {[0, 1, 2].map((i) => (
        <motion.span key={i} aria-hidden
          className="absolute inset-0 rounded-full border border-hairline"
          style={reduced ? { opacity: 0.35 + 0.65 * level, inset: -i * 18 } : { scale, inset: -i * 18, opacity: 1 - i * 0.25 }} />
      ))}
      <div className="grid size-full place-items-center rounded-full border border-hairline bg-paper font-display text-[96px] leading-none text-ink">
        {name.slice(0, 1).toUpperCase()}
      </div>
    </div>
  );
}

function TaskStrip({ conversationId }: { conversationId: string | null }) {
  const task = useTasks((s) => Object.values(s.tasks).find((t) => t.conversationId === conversationId && !TERMINAL_STATES.includes(t.state)));
  if (!task) return null;
  const step = task.steps[task.steps.length - 1]?.summary ?? "Planning…";
  return (
    <button type="button" data-testid="task-strip"
      onClick={() => { useUi.getState().openTaskPanel(task.id); useUi.getState().setCallOpen(false); }}
      className="flex w-full max-w-md items-center gap-3 rounded-lg border border-hairline bg-paper px-3 py-2 text-left text-[12.5px]">
      <span className="truncate font-medium text-ink">{task.goal}</span>
      <span className="truncate text-muted">{step}</span>
      {task.plan.length > 0 && <span className="ml-auto shrink-0 text-faint">{task.planDone.length}/{task.plan.length}</span>}
    </button>
  );
}

export function CallView() {
  const { state, muted, captions, speaking, personaName, conversationId, error, endCall, toggleMute } = useVoice();
  const level = useLevel(state);
  const lastUser = [...captions].reverse().find((c) => c.role === "user")?.text ?? "";
  const hide = () => useUi.getState().setCallOpen(false);
  const finish = () => { void endCall(); hide(); };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLElement && ["INPUT", "TEXTAREA"].includes(e.target.tagName)) return;
      if (e.key === " ") { e.preventDefault(); toggleMute(); }
      if (e.key === "Escape") { e.preventDefault(); finish(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const live = LIVE.includes(state);
  return (
    <section aria-label="Call" className="absolute inset-0 z-30 flex flex-col items-center justify-center gap-8 bg-paper px-10">
      <Portrait name={personaName} level={live ? level : 0} />
      <div className="flex min-h-[120px] max-w-xl flex-col items-center gap-3 text-center">
        {speaking && <div className="font-voice text-[22px] leading-8 text-ink"><InkText text={speaking} /></div>}
        {lastUser && <p className="text-[13px] text-muted">{lastUser}</p>}
        {error && <p role="alert" className="text-[13px] text-accent">{error}</p>}
      </div>
      <TaskStrip conversationId={conversationId} />
      <div className="flex flex-col items-center gap-3">
        <span className="text-[11px] uppercase tracking-[0.14em] text-muted" data-testid="call-state">{stateLine(state, muted)}</span>
        <div className="flex items-center gap-3">
          {live ? (
            <>
              <button type="button" aria-label={muted ? "Unmute" : "Mute"} aria-pressed={muted} onClick={toggleMute}
                className="grid size-11 place-items-center rounded-full border border-hairline text-ink hover:border-muted">
                {muted ? <MicOff size={18} /> : <Mic size={18} />}
              </button>
              <button type="button" aria-label="End call" onClick={finish}
                className="grid size-11 place-items-center rounded-full bg-accent text-canvas">
                <PhoneOff size={18} />
              </button>
            </>
          ) : (
            <button type="button" onClick={hide} className="rounded-full border border-hairline px-4 py-2 text-[13px] text-ink">Close</button>
          )}
          <button type="button" aria-label="Show chat" onClick={hide}
            className="grid size-11 place-items-center rounded-full border border-hairline text-ink hover:border-muted">
            <MessageSquare size={18} />
          </button>
        </div>
        {live && <span className="text-[11.5px] text-faint">Space mutes · Esc ends the call</span>}
      </div>
    </section>
  );
}
