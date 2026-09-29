import { Button } from "../../ui/Button";
import { cn } from "../../ui/cn";
import { Sparkline } from "./Sparkline";
import { useSetSkillStatus, type Skill } from "./memoryApi";

const STATUS_LABEL = { approved: "in use", quarantined: "waiting for you", deprecated: "retired" } as const;

export function SkillCard({ skill }: { skill: Skill }) {
  const setStatus = useSetSkillStatus();
  const rate = skill.runs ? Math.round((skill.successes / skill.runs) * 100) : null;
  const history = skill.duration_history;
  return (
    <article aria-labelledby={`skill-${skill.id}`} className={cn("rounded-xl border border-hairline bg-paper p-4", skill.status === "deprecated" && "opacity-60")}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id={`skill-${skill.id}`} className="font-display text-[20px] leading-tight text-ink">{skill.title}</h3>
          <p className="mt-1 font-voice text-[14px] text-ink-2">{skill.intent}</p>
        </div>
        <span className={cn("shrink-0 rounded-full border px-2 py-0.5 text-[11px]", skill.status === "quarantined" ? "border-accent text-accent" : "border-hairline text-muted")}>
          {STATUS_LABEL[skill.status]}
        </span>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-muted">
        <span>{skill.runs === 0 ? "not used yet" : `${skill.runs} run${skill.runs === 1 ? "" : "s"} · ${rate}% worked`}</span>
        {skill.avg_duration_s != null && <span>about {Math.round(skill.avg_duration_s)} s lately</span>}
        {skill.apps.length > 0 && <span>{skill.apps.join(", ")}</span>}
        <Sparkline values={history} label={`Durations, first to latest: ${history.map((d) => `${Math.round(d)} s`).join(", ")}`} />
      </div>
      <details className="mt-3 text-[13px] text-ink-2">
        <summary className="cursor-pointer text-muted">Steps{skill.pitfalls.length ? " and pitfalls" : ""}</summary>
        <ol className="mt-2 list-decimal space-y-1 pl-5">{skill.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
        {skill.pitfalls.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-5 text-muted">{skill.pitfalls.map((p, i) => <li key={i}>{p}</li>)}</ul>}
      </details>
      <div className="mt-3 flex gap-2">
        {skill.status !== "approved" && (
          <Button variant="primary" onClick={() => setStatus.mutate({ id: skill.id, status: "approved" })}>
            {skill.status === "deprecated" ? "Use again" : "Approve"}
          </Button>
        )}
        {skill.status !== "deprecated" && (
          <Button onClick={() => setStatus.mutate({ id: skill.id, status: "deprecated" })}>Retire</Button>
        )}
      </div>
    </article>
  );
}
