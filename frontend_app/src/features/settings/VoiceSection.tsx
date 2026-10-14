import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../../lib/api";
import { saveKey } from "../../lib/keys";
import type { AppSettings } from "../../lib/types";
import { Button } from "../../ui/Button";
import { Field, inputClass } from "../../ui/Field";
import { Section } from "./Section";
import { useUpdateSettings } from "./useSettings";

interface VoiceInfo {
  stt: { name: string; azure: boolean; loaded: boolean };
  tts: { kokoro: boolean; voices: number; azure: boolean };
  last_turn_ms: number | null;
}

export function describeVoice(info: VoiceInfo): string {
  const hears = info.stt.name.startsWith("azure:")
    ? `Hears you with Azure Speech (${info.stt.name.slice(6)}).`
    : "Hears you on this PC" + (info.stt.loaded ? "." : " (the model loads when a call starts).");
  const last = info.last_turn_ms == null ? "" : ` Last call: ${(info.last_turn_ms / 1000).toFixed(1)} s to the first sound.`;
  return hears + last;
}

const why = (e: unknown) => (e instanceof Error && e.message ? e.message : "That didn't work.");
const selectClass = "h-9 w-72 rounded-lg border border-hairline bg-paper px-2 text-[13px] text-ink";

export function VoiceSection({ settings }: { settings: AppSettings }) {
  const qc = useQueryClient();
  const update = useUpdateSettings();
  const [key, setKey] = useState("");
  const [region, setRegion] = useState(settings.voice.azure_region);
  const [language, setLanguage] = useState(settings.voice.language);
  const { data: answer } = useQuery({ queryKey: ["voice"], queryFn: () => api<VoiceInfo>("/api/voice/status") });
  const info = typeof answer?.stt?.name === "string" ? answer : undefined; // not yet known (or not an answer)
  const { data: keys } = useQuery({ queryKey: ["keys"], queryFn: () => api<Record<string, boolean>>("/api/keys/status") });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["voice"] });
    void qc.invalidateQueries({ queryKey: ["keys"] });
  };
  const hasKey = keys?.azure_speech === true;
  const save = useMutation({
    mutationFn: (value: string | null) => saveKey("azure_speech", value),
    onSuccess: (_d, value) => { setKey(""); toast(value ? "Azure Speech key saved" : "Azure Speech key removed"); refresh(); },
    onError: (e) => toast.error(why(e)),
  });
  const saveText = (patch: Partial<AppSettings["voice"]>) =>
    update.mutate({ voice: patch }, { onSuccess: refresh, onError: (e) => toast.error(why(e)) });
  const check = useMutation({
    mutationFn: () => api<{ ok: boolean; message: string }>("/api/voice/azure-check", { method: "POST" }),
    onSuccess: (r) => (r.ok ? toast(r.message) : toast.error(r.message)),
    onError: (e) => toast.error(why(e)),
  });
  return (
    <Section title="Voice" description="Speaking with Aethel in a call. Azure Speech hears you and can speak back; Aethel's own voice runs on this PC. In private mode nothing is sent to Azure.">
      <Field label="Status">
        <span className="text-[13px] text-muted">{info ? describeVoice(info) : "Checking…"}</span>
      </Field>
      <Field label="Azure Speech key" hint="From your Speech resource in the Azure portal (Keys and Endpoint). It stays on this PC, in Windows Credential Manager." htmlFor="azure-key">
        <div className="flex items-center gap-2">
          <span className={hasKey ? "size-1.5 rounded-full bg-accent" : "size-1.5 rounded-full bg-hairline"} title={hasKey ? "Key saved" : "No key"} />
          <input id="azure-key" aria-label="Azure Speech key" type="password" autoComplete="off"
            placeholder={hasKey ? "•••••••• saved" : "Paste the key"} value={key} onChange={(e) => setKey(e.target.value)}
            className={`${inputClass} w-56`} />
          <Button disabled={!key.trim() || save.isPending} onClick={() => save.mutate(key.trim())}>Save</Button>
          {hasKey && <Button variant="danger" disabled={save.isPending} onClick={() => save.mutate(null)}>Remove</Button>}
        </div>
      </Field>
      <Field label="Azure region" hint="The region of that resource, e.g. uksouth or westeurope." htmlFor="azure-region">
        <div className="flex items-center gap-2">
          <input id="azure-region" aria-label="Azure region" placeholder="uksouth" value={region}
            onChange={(e) => setRegion(e.target.value.trim().toLowerCase())} className={`${inputClass} w-56`} />
          <Button disabled={region === settings.voice.azure_region} onClick={() => saveText({ azure_region: region })}>Save</Button>
          <Button disabled={!hasKey || !settings.voice.azure_region || check.isPending} onClick={() => check.mutate()}>Check</Button>
        </div>
      </Field>
      <Field label="Language you speak" hint="Azure needs to know, e.g. en-GB or en-US." htmlFor="voice-language">
        <div className="flex items-center gap-2">
          <input id="voice-language" aria-label="Language you speak" value={language}
            onChange={(e) => setLanguage(e.target.value.trim())} className={`${inputClass} w-56`} />
          <Button disabled={language === settings.voice.language} onClick={() => saveText({ language })}>Save</Button>
        </div>
      </Field>
      <Field label="Hear me with" hint="Without a key, in private mode, or when Azure can't be reached, the speech model on this PC is used.">
        <select aria-label="Hear me with" className={selectClass} value={settings.voice.stt}
          onChange={(e) => saveText({ stt: e.target.value as "azure" | "local" })}>
          <option value="azure">Azure Speech</option>
          <option value="local">This PC</option>
        </select>
      </Field>
      <Field label="Speak with" hint="Aethel's own voice supports the blends you design for a persona. Azure's voices sound smoother but are online.">
        <select aria-label="Speak with" className={selectClass} value={settings.voice.tts}
          onChange={(e) => saveText({ tts: e.target.value as "kokoro" | "azure" })}>
          <option value="kokoro">Aethel's voice (this PC)</option>
          <option value="azure">Azure neural voice</option>
        </select>
      </Field>
    </Section>
  );
}
