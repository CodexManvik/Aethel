import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { Field } from "../../ui/Field";
import { Section } from "./Section";

const LABELS: Record<string, string> = { windows: "Desktop control", office: "Word, Excel and PowerPoint", files: "File search and editing" };

export function describeServer(status: string): string {
  if (status === "running") return "Connected";
  if (status === "starting") return "Starting… (the first start downloads it)";
  if (status === "stopped") return "Stopped";
  return `Not available: ${status.replace(/^failed: /, "")}`;
}

export function ToolsSection() {
  const { data } = useQuery({
    queryKey: ["tools"],
    queryFn: () => api<{ servers: Record<string, string>; tools: string[] }>("/api/tools"),
    refetchInterval: 5000, // servers connect in the background after launch
  });
  const servers = Object.entries(data?.servers ?? {});
  return (
    <Section title="Computer control" description="What tasks can use besides files and commands. Ctrl+Alt+Esc stops any task at once.">
      {servers.length === 0 && <p className="py-4 text-[13px] text-muted">Computer control is off.</p>}
      {servers.map(([name, status]) => (
        <Field key={name} label={LABELS[name] ?? name}>
          <span className={status === "running" ? "text-[13px] text-ink" : "text-[13px] text-muted"}>{describeServer(status)}</span>
        </Field>
      ))}
    </Section>
  );
}
