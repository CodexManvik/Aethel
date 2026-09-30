export interface UsageTotals {
  prompt: number;
  completion: number;
  cached: number;
  calls: number;
  estimated: boolean;
}

export interface UsageReport {
  days: number;
  total: UsageTotals;
  by_purpose: Record<string, UsageTotals>;
  by_day: (UsageTotals & { day: string })[];
}

export const PURPOSE_LABEL: Record<string, string> = {
  chat_reply: "Replies",
  fact_extract: "Remembering facts",
  plan: "Planning tasks",
  execute: "Doing tasks",
  final_summary: "Task summaries",
  reflect: "Learning from tasks",
  vision_locate: "Finding things on screen",
  web_chat: "Web answers",
};

export const formatTokens = (n: number): string => (n < 1000 ? String(n) : `${(n / 1000).toFixed(1)}k`);

/** "≈12.4k in · 0.9k out · 3.1k cached · 7 calls" */
export function describeUsage(u: UsageTotals): string {
  const parts = [`${u.estimated ? "≈" : ""}${formatTokens(u.prompt)} in`, `${formatTokens(u.completion)} out`];
  if (u.cached > 0) parts.push(`${formatTokens(u.cached)} cached`);
  parts.push(`${u.calls} call${u.calls === 1 ? "" : "s"}`);
  return parts.join(" · ");
}
