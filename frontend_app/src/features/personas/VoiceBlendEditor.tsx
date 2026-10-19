import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";
import { toast } from "sonner";
import { api } from "../../lib/api";
import { Button } from "../../ui/Button";
import { normaliseBlend, previewVoice, type PersonaVoice } from "./personasApi";

const MAX_VOICES = 3;
const selectClass = "h-9 w-44 rounded-lg border border-hairline bg-paper px-2 text-[13px] text-ink outline-none focus:border-muted";
type Row = { voice: string; weight: number };

const rowsOf = (v: PersonaVoice): Row[] =>
  Object.entries(v.blend).sort((a, b) => b[1] - a[1]).map(([voice, weight]) => ({ voice, weight }));

interface Props {
  name: string;
  value: PersonaVoice;
  onChange(voice: PersonaVoice): void;
}

/** Up to three of Kokoro's voices mixed by weight, and a speed. Weights are normalised when they're saved. */
export function VoiceBlendEditor({ name, value, onChange }: Props) {
  const [rows, setRows] = useState<Row[]>(() => rowsOf(value));
  const [speed, setSpeed] = useState(value.speed);
  const { data: voices } = useQuery({ queryKey: ["voices"], queryFn: () => api<Record<string, string[]>>("/api/voice/voices") });
  const groups = Object.entries(voices && typeof voices === "object" ? voices : {}).filter(([, n]) => Array.isArray(n));
  const known = new Set(groups.flatMap(([, n]) => n));
  const first = groups[0]?.[1][0] ?? "af_heart";

  const commit = (next: Row[], nextSpeed = speed) => {
    setRows(next);
    setSpeed(nextSpeed);
    onChange({ blend: normaliseBlend(next), speed: nextSpeed });
  };
  const preview = useMutation({
    mutationFn: () => previewVoice(normaliseBlend(rows), speed, `Hi, it's ${name}. How's your day going?`),
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  });

  return (
    <div className="flex flex-col gap-3">
      {rows.map((r, i) => (
        <div key={i} className="flex items-center gap-3">
          <select aria-label={`Voice ${i + 1}`} className={selectClass} value={r.voice}
            onChange={(e) => commit(rows.map((x, j) => (j === i ? { ...x, voice: e.target.value } : x)))}>
            {groups.map(([lang, names]) => (
              <optgroup key={lang} label={lang}>{names.map((n) => <option key={n} value={n}>{n}</option>)}</optgroup>
            ))}
            {!known.has(r.voice) && <option value={r.voice}>{r.voice}</option>}
          </select>
          <input type="range" aria-label={`Weight of voice ${i + 1}`} min={0} max={1} step={0.05} value={r.weight}
            className="w-36 accent-[var(--accent)]"
            onChange={(e) => commit(rows.map((x, j) => (j === i ? { ...x, weight: Number(e.target.value) } : x)))} />
          <span className="w-10 text-right text-[12px] tabular-nums text-muted">
            {Math.round((r.weight / (rows.reduce((s, x) => s + x.weight, 0) || 1)) * 100)}%
          </span>
          {rows.length > 1 && (
            <button type="button" aria-label={`Remove voice ${i + 1}`} className="text-faint hover:text-ink"
              onClick={() => commit(rows.filter((_, j) => j !== i))}><X size={15} /></button>
          )}
        </div>
      ))}
      <div className="flex items-center gap-3">
        {rows.length < MAX_VOICES && (
          <Button onClick={() => commit([...rows, { voice: first, weight: 0.3 }])}><Plus size={14} /> Add a voice</Button>
        )}
        <label className="flex items-center gap-2 text-[12.5px] text-muted">
          Speed
          <input type="range" aria-label="Speed" min={0.7} max={1.3} step={0.05} value={speed}
            className="w-28 accent-[var(--accent)]" onChange={(e) => commit(rows, Number(e.target.value))} />
          <span className="w-9 tabular-nums">{speed.toFixed(2)}×</span>
        </label>
        <Button disabled={preview.isPending} onClick={() => preview.mutate()}>Preview</Button>
      </div>
    </div>
  );
}
