import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../../lib/api";
import type { AppSettings } from "../../lib/types";
import { Button } from "../../ui/Button";
import { Field } from "../../ui/Field";
import { Switch } from "../../ui/Switch";
import { Section } from "./Section";
import { useUpdateSettings } from "./useSettings";

interface BrowserInfo {
  status: string;
  show: boolean;
  profile_exists: boolean;
  signing_in: boolean;
}

export function describeBrowser(status: string): string {
  if (status === "running") return "Ready. Edge opens in the background when a task needs it.";
  if (status === "starting") return "Starting… (the first start downloads it)";
  if (status === "absent") return "Not available: it needs Node.js, which isn't installed.";
  if (status === "stopped") return "Stopped";
  return `Not available: ${status.replace(/^failed: /, "")}`;
}

const why = (e: unknown) => (e instanceof Error && e.message ? e.message : "That didn't work.");

export function BrowserSection({ settings }: { settings: AppSettings }) {
  const qc = useQueryClient();
  const update = useUpdateSettings();
  const [confirming, setConfirming] = useState(false);
  const { data: answer } = useQuery({
    queryKey: ["browser"],
    queryFn: () => api<BrowserInfo>("/api/browser"),
    refetchInterval: 4000, // it starts in the background, and the sign-in window closes on its own
  });
  const data = typeof answer?.status === "string" ? answer : undefined; // not yet known (or not an answer)
  const refresh = () => void qc.invalidateQueries({ queryKey: ["browser"] });
  const signIn = useMutation({
    mutationFn: () => api("/api/browser/sign-in", { method: "POST" }),
    onSuccess: () => { toast("A browser window is open. Sign in to your sites, then close it."); refresh(); },
    onError: (e) => toast.error(why(e)),
  });
  const restart = useMutation({
    mutationFn: () => api("/api/browser/restart", { method: "POST" }),
    onSuccess: () => { toast("The browser was restarted."); refresh(); },
    onError: (e) => toast.error(why(e)),
  });
  const clear = useMutation({
    mutationFn: () => api("/api/browser/profile", { method: "DELETE" }),
    onSuccess: () => { toast("The browser's saved data was cleared."); refresh(); },
    onError: (e) => toast.error(why(e)),
    onSettled: () => setConfirming(false),
  });
  const unavailable = !data || data.status === "absent" || data.status.startsWith("failed");
  return (
    <Section title="Browser" description="Aethel's own browser, for looking things up and filling in forms in the background. It keeps its own sign-ins, separate from yours.">
      <Field label="Status">
        <span className={data?.status === "running" ? "text-[13px] text-ink" : "text-[13px] text-muted"}>
          {data ? describeBrowser(data.status) : "Checking…"}
        </span>
      </Field>
      <Field label="Show browser" hint="Watch it work in a window. This takes effect the next time the browser starts.">
        <div className="flex items-center gap-3">
          {data && data.show !== settings.browser.show && (
            <Button disabled={unavailable || restart.isPending} onClick={() => restart.mutate()}>Restart now</Button>
          )}
          <Switch label="Show browser" checked={settings.browser.show}
            onCheckedChange={(v) => update.mutate({ browser: { show: v } })} />
        </div>
      </Field>
      <Field label="Sign in to sites…" hint="Opens the browser in a window so you can log in yourself. Aethel never types passwords or codes. Close the window when you're done.">
        <Button disabled={unavailable || data?.signing_in || signIn.isPending} onClick={() => signIn.mutate()}>
          {data?.signing_in ? "Sign-in window open" : "Sign in…"}
        </Button>
      </Field>
      <Field label="Clear browser data" hint="Forget every sign-in, cookie and page the browser has saved.">
        {confirming ? (
          <div role="group" aria-label="Confirm clearing browser data" className="flex items-center gap-2">
            <span className="text-[12.5px] text-muted">Clear everything it knows?</span>
            <Button variant="danger" disabled={clear.isPending} onClick={() => clear.mutate()}>Yes, clear it</Button>
            <Button onClick={() => setConfirming(false)}>Cancel</Button>
          </div>
        ) : (
          <Button disabled={!data || !data.profile_exists} onClick={() => setConfirming(true)}>
            {data && !data.profile_exists ? "Nothing saved yet" : "Clear browser data…"}
          </Button>
        )}
      </Field>
    </Section>
  );
}
