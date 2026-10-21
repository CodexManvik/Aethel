import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, apiBlob } from "../../lib/api";
import { saveKey } from "../../lib/keys";
import type { AppSettings } from "../../lib/types";
import { Button } from "../../ui/Button";
import { Field, inputClass } from "../../ui/Field";
import { Switch } from "../../ui/Switch";
import { DownloadButton } from "./Downloads";
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
  const { data: voices } = useQuery({ queryKey: ["voices"], queryFn: () => api<Record<string, string[]>>("/api/voice/voices") });
  const blend = settings.voice.default_blend ?? {};
  const mainVoice = Object.entries(blend).filter(([k]) => k !== "speed").sort((a, b) => b[1] - a[1])[0]?.[0] ?? "af_heart";
  const [wait, setWait] = useState(settings.voice.max_wait_ms);
  const preview = useMutation({
    mutationFn: async () => {
      const blob = await apiBlob("/api/voice/preview", { method: "POST",
        body: JSON.stringify({ blend, text: "Hello, this is how I sound." }) });
      // Through Web Audio, not <audio src="blob:…">: the app's CSP allows no blob: media.
      const ctx = new AudioContext();
      const source = ctx.createBufferSource();
      source.buffer = await ctx.decodeAudioData(await blob.arrayBuffer());
      source.connect(ctx.destination);
      source.onended = () => void ctx.close();
      source.start();
    },
    onError: (e) => toast.error(why(e)),
  });
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
      {info && info.tts && !info.tts.kokoro && (
        <Field label="Aethel's own voice" hint="Kokoro's voices aren't on this PC yet (about 350 MB, from its GitHub release, checked against a pinned hash).">
          <DownloadButton name="voices" label="Download the voices" onDone={refresh} />
        </Field>
      )}
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
      <Field label="Hear me with" hint="Without a key, in private mode, or when Azure can't be reached, the speech model on this PC is used. On a laptop it takes several seconds per sentence.">
        <select aria-label="Hear me with" className={selectClass} value={settings.voice.stt}
          onChange={(e) => saveText({ stt: e.target.value as "azure" | "local" })}>
          <option value="azure">Azure Speech</option>
          <option value="local">This PC (slow)</option>
        </select>
      </Field>
      <Field label="Speak with" hint="Aethel's own voice supports the blends you design for a persona. Azure's voices sound smoother but are online.">
        <select aria-label="Speak with" className={selectClass} value={settings.voice.tts}
          onChange={(e) => saveText({ tts: e.target.value as "kokoro" | "azure" })}>
          <option value="kokoro">Aethel's voice (this PC)</option>
          <option value="azure">Azure neural voice</option>
        </select>
      </Field>
      <Field label="Aethel's voice" hint="Used when speaking on this PC. Personas get their own voices later." htmlFor="voice-pick">
        <div className="flex items-center gap-2">
          <select id="voice-pick" aria-label="Aethel's voice" className={`${selectClass} w-56`} value={mainVoice}
            onChange={(e) => saveText({ default_blend: { [e.target.value]: 1, speed: blend.speed ?? 1 } })}>
            {Object.entries(voices && typeof voices === "object" ? voices : {}).map(([lang, names]) => (
              <optgroup key={lang} label={lang}>
                {(Array.isArray(names) ? names : []).map((n) => <option key={n} value={n}>{n}</option>)}
              </optgroup>
            ))}
            {!(voices && Object.values(voices).some((n) => Array.isArray(n) && n.includes(mainVoice))) && <option value={mainVoice}>{mainVoice}</option>}
          </select>
          <Button disabled={preview.isPending} onClick={() => preview.mutate()}>Preview</Button>
        </div>
      </Field>
      <Field label="Wait before replying" hint="How long a pause has to be before Aethel answers. Shorter feels quicker; longer cuts you off less." htmlFor="voice-wait">
        <div className="flex items-center gap-3">
          <input id="voice-wait" aria-label="Wait before replying" type="range" min={800} max={2500} step={100} value={wait}
            onChange={(e) => setWait(Number(e.target.value))} onMouseUp={() => saveText({ max_wait_ms: wait })}
            onKeyUp={() => saveText({ max_wait_ms: wait })} onBlur={() => wait !== settings.voice.max_wait_ms && saveText({ max_wait_ms: wait })} />
          <span className="w-12 text-right text-[12.5px] tabular-nums text-muted">{(wait / 1000).toFixed(1)} s</span>
        </div>
      </Field>
      <Field label="Short acknowledgements" hint={'A quick "Mm." when the answer takes a moment to start. Not saved.'}>
        <Switch label="Short acknowledgements" checked={settings.voice.ack} onCheckedChange={(v) => saveText({ ack: v })} />
      </Field>
      <Field label="Talk me through tasks" hint="During a call, say now and then how a task is going, and read out how it ended.">
        <Switch label="Talk me through tasks" checked={settings.voice.narration} onCheckedChange={(v) => saveText({ narration: v })} />
      </Field>
    </Section>
  );
}
