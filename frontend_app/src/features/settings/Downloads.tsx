import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../../lib/api";
import { Button } from "../../ui/Button";

export type DownloadName = "voices" | "llama-vulkan" | "llama-cpu" | "local-images";

interface Item {
  installed: boolean;
  running: boolean;
  progress: number;
  error: string | null;
}

export type DownloadsStatus = Record<DownloadName, Item> & { packages_installable: boolean };

export function useDownloads() {
  return useQuery({
    queryKey: ["downloads"],
    queryFn: () => api<DownloadsStatus>("/api/downloads"),
    refetchInterval: (q) => {
      const d = q.state.data;
      return d && typeof d === "object" && Object.values(d).some((v) => typeof v === "object" && v?.running) ? 1000 : false;
    },
  });
}

/** A button that fetches something pinned (spec 2026-10-19 §I3), its progress while it runs, and why it stopped. */
export function DownloadButton({ name, label, done, onDone }: { name: DownloadName; label: string; done?: string; onDone?(): void }) {
  const qc = useQueryClient();
  const { data } = useDownloads();
  const item = data && typeof data === "object" ? (data[name] as Item | undefined) : undefined;
  const start = useMutation({
    mutationFn: () => api(`/api/downloads/${name}`, { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["downloads"] }),
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  });
  const wasRunning = useRef(false);
  const running = item?.running ?? false;
  const installed = item?.installed ?? false;
  useEffect(() => { // once, when a download that was running here finishes
    if (wasRunning.current && !running && installed) onDone?.();
    wasRunning.current = running;
  }, [running, installed]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!item) return null;
  if (item.installed && !item.running) return done ? <span className="text-[12px] text-faint">{done}</span> : null;
  return (
    <div className="flex flex-col items-end gap-1">
      {item.running ? (
        <div className="h-1.5 w-48 overflow-hidden rounded-full bg-well" role="progressbar" aria-label={label}
          aria-valuenow={Math.round(item.progress * 100)}>
          <div className="h-full bg-accent transition-[width]" style={{ width: `${Math.max(4, item.progress * 100)}%` }} />
        </div>
      ) : (
        <Button disabled={start.isPending} onClick={() => start.mutate()}>{label}</Button>
      )}
      {item.error && <p role="alert" className="max-w-xs text-right text-[12px] text-accent">{item.error}</p>}
    </div>
  );
}
