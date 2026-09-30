import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { formatTokens, PURPOSE_LABEL, type UsageReport, type UsageTotals } from "../../lib/usage";
import { Section } from "./Section";

function Row({ label, u, strong }: { label: string; u: UsageTotals; strong?: boolean }) {
  return (
    <tr className={strong ? "font-medium text-ink" : "text-ink-2"}>
      <th scope="row" className="py-1.5 pr-4 text-left font-normal">{label}</th>
      <td className="py-1.5 pr-4 text-right tabular-nums">{u.estimated ? "≈" : ""}{formatTokens(u.prompt)}</td>
      <td className="py-1.5 pr-4 text-right tabular-nums">{formatTokens(u.completion)}</td>
      <td className="py-1.5 pr-4 text-right tabular-nums">{formatTokens(u.cached)}</td>
      <td className="py-1.5 text-right tabular-nums">{u.calls}</td>
    </tr>
  );
}

export function UsageSection() {
  const { data } = useQuery({ queryKey: ["usage", 7], queryFn: () => api<UsageReport>("/api/usage?days=7") });
  const purposes = data?.by_purpose ? Object.entries(data.by_purpose).sort((a, b) => b[1].prompt - a[1].prompt) : [];
  return (
    <Section title="Usage" description="Tokens sent to and received from language models in the last 7 days. ≈ means the provider didn't say, so it's an estimate.">
      {!data?.total?.calls ? (
        <p className="py-3 font-voice text-[15px] text-muted">Nothing yet.</p>
      ) : (
        <table className="mt-1 w-full text-[13px]">
          <thead>
            <tr className="text-[11px] uppercase tracking-[0.12em] text-faint">
              <th scope="col" className="pb-1 text-left font-normal">For</th>
              <th scope="col" className="pb-1 pr-4 text-right font-normal">In</th>
              <th scope="col" className="pb-1 pr-4 text-right font-normal">Out</th>
              <th scope="col" className="pb-1 pr-4 text-right font-normal">Cached</th>
              <th scope="col" className="pb-1 text-right font-normal">Calls</th>
            </tr>
          </thead>
          <tbody>
            {purposes.map(([p, u]) => <Row key={p} label={PURPOSE_LABEL[p] ?? p} u={u} />)}
            <Row label="Total" u={data.total} strong />
          </tbody>
        </table>
      )}
    </Section>
  );
}
