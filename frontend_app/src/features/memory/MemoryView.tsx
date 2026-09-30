import { useRef, useState, type KeyboardEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { cn } from "../../ui/cn";
import { FactsTab } from "./FactsTab";
import { NotesTab } from "./NotesTab";
import { SkillCard } from "./SkillCard";
import { useSkills } from "./memoryApi";

const TABS = [
  { id: "facts", label: "Facts" },
  { id: "skills", label: "Skills" },
  { id: "notes", label: "App notes" },
] as const;
type TabId = (typeof TABS)[number]["id"];

const S1_LABEL: Record<string, string> = {
  ready: "System 1 is ready.",
  downloading: "System 1 is downloading its model (about 1.7 GB, first run only).",
  loading: "System 1 is loading.",
  off: "System 1 is off.",
  "not loaded": "System 1 hasn't started.",
};

export function describeSystem1(status: string): string {
  return S1_LABEL[status] ?? `System 1 isn't available: ${status.replace(/^failed: /, "")}`;
}

const ORDER = { quarantined: 0, approved: 1, deprecated: 2 };

export function MemoryView() {
  const [tab, setTab] = useState<TabId>("facts");
  const refs = useRef<Partial<Record<TabId, HTMLButtonElement | null>>>({});
  const { data: skills = [] } = useSkills();
  const { data: s1 } = useQuery({
    queryKey: ["memory", "system1"],
    queryFn: () => api<{ status: string }>("/api/memory/system1"),
    refetchInterval: 5000, // the model downloads and loads in the background after launch
  });
  const sorted = [...skills].sort((a, b) => ORDER[a.status] - ORDER[b.status] || b.runs - a.runs);

  // Tabs pattern: arrow keys move between tabs, Home/End jump to the ends.
  const onKey = (e: KeyboardEvent) => {
    const i = TABS.findIndex((t) => t.id === tab);
    const keys: Record<string, number> = { ArrowRight: (i + 1) % TABS.length, ArrowLeft: (i + TABS.length - 1) % TABS.length, Home: 0, End: TABS.length - 1 };
    if (!(e.key in keys)) return;
    e.preventDefault();
    const next = TABS[keys[e.key]].id;
    setTab(next);
    refs.current[next]?.focus();
  };

  return (
    <div className="h-full overflow-y-auto bg-paper">
      <div className="mx-auto max-w-3xl px-10 py-10">
        <h1 className="font-display text-[44px] leading-none">Memory</h1>
        <p className="mt-2 font-voice text-[15px] text-muted">
          What Aethel knows about you and has learned from its tasks.{s1 ? ` ${describeSystem1(s1.status)}` : ""}
        </p>
        <div role="tablist" aria-label="Memory" className="mt-6 flex gap-5 border-b border-hairline" onKeyDown={onKey}>
          {TABS.map((t) => (
            <button
              key={t.id}
              ref={(el) => { refs.current[t.id] = el; }}
              type="button"
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls={`panel-${t.id}`}
              tabIndex={tab === t.id ? 0 : -1}
              onClick={() => setTab(t.id)}
              className={cn("-mb-px border-b-2 pb-2 text-[14px]", tab === t.id ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink")}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
          {tab === "facts" ? (
            <FactsTab />
          ) : tab === "notes" ? (
            <NotesTab />
          ) : sorted.length === 0 ? (
            <p className="py-6 font-voice text-[15px] text-muted">
              No skills yet. After a task succeeds, Aethel writes down how it did it and reuses that next time.
            </p>
          ) : (
            <div className="grid gap-4 py-4">{sorted.map((s) => <SkillCard key={s.id} skill={s} />)}</div>
          )}
        </div>
      </div>
    </div>
  );
}
