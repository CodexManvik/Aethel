import { create } from "zustand";
import { startCapture, type Capture, type CaptureCallbacks } from "../features/call/audio/capture";
import { Playback, type SentenceMeta } from "../features/call/audio/playback";
import { openVoiceSocket, voiceUrl, type VoiceHandlers, type VoiceTransport } from "../features/call/voiceSocket";
import type { Server } from "../lib/voice.gen";

export type CallState = "idle" | "connecting" | "listening" | "hearing" | "thinking" | "speaking" | "ended" | "error";
export interface CallCaption { role: "user" | "assistant"; text: string; final: boolean }
const MAX_CAPTIONS = 6;

/** What a call needs from the outside world; tests replace it. */
export interface VoiceDeps {
  open(h: VoiceHandlers): Promise<VoiceTransport>;
  capture(cb: CaptureCallbacks): Promise<Capture>;
  playback(): Playback;
}

let deps: VoiceDeps = {
  open: async (h) => openVoiceSocket(await voiceUrl(), h),
  capture: startCapture,
  playback: () => new Playback(),
};
export function setVoiceDeps(next: Partial<VoiceDeps>) { deps = { ...deps, ...next }; }

interface VoiceState {
  state: CallState;
  conversationId: string | null;
  personaName: string;
  muted: boolean;
  captions: CallCaption[];
  speaking: string;      // the sentence being played now
  error: string | null;
  inputLevel: number;
  outputLevel(): number;
  startCall(conversationId: string): Promise<void>;
  endCall(): Promise<void>;
  toggleMute(): void;
}

// The live call's machinery: kept out of the store's state, which is for rendering.
let socket: VoiceTransport | null = null;
let capture: Capture | null = null;
let playback: Playback | null = null;
let metas = new Map<number, SentenceMeta & { text: string }>();
let activeTurn: string | null = null; // the turn the server is producing: from its `thinking` to its turn_end
let dropped = new Set<string>();      // turns the user spoke over: their late audio is not played
let generation = 0;                   // calls started; a late event from an older call is ignored

export const useVoice = create<VoiceState>((set, get) => {
  const caption = (c: CallCaption) => set((s) => ({ captions: [...s.captions, c].slice(-MAX_CAPTIONS) }));
  const settle = () => { if (!activeTurn && !playback?.playing && get().state !== "hearing") set({ state: "listening" }); };

  const teardown = (state: CallState, error: string | null = null) => {
    generation += 1;
    const s = socket, c = capture, p = playback;
    socket = capture = playback = null;
    metas = new Map();
    activeTurn = null;
    dropped = new Set();
    s?.close();
    c?.stop().catch(() => {});
    p?.close();
    set({ state, error, speaking: "", inputLevel: 0 });
  };

  const onMessage = (msg: Server) => {
    switch (msg.type) {
      case "ready":
        set({ personaName: msg.persona_name });
        return void startListening();
      case "caption":
        return caption({ role: msg.role, text: msg.text, final: msg.final });
      case "thinking":
        activeTurn = msg.turn_id;
        if (get().state !== "hearing") set({ state: "thinking" });
        return;
      case "no_speech":
        if (!activeTurn && get().state === "thinking") set({ state: "listening" });
        return;
      case "tts_meta":
        if (dropped.has(msg.turn_id)) return;
        metas.set(msg.sentence_id, { sentenceId: msg.sentence_id, turnId: msg.turn_id, sampleRate: msg.sample_rate, text: msg.text });
        return;
      case "turn_end":
        if (activeTurn === msg.turn_id) activeTurn = null;
        return settle();
      case "voice_error":
        if (msg.fatal) return teardown("error", msg.message);
        set({ error: msg.message });
        return settle(); // e.g. speech recognition failed: nothing more is coming for that utterance
    }
  };

  const onAudio = (sentenceId: number, audio: ArrayBuffer) => {
    const meta = metas.get(sentenceId);
    if (!meta || !playback) return;
    metas.delete(sentenceId);
    playback.enqueue(meta, audio);
    if (get().state !== "hearing") set({ state: "speaking" });
  };

  const startListening = async () => {
    const mine = generation;
    try {
      const c = await deps.capture({
        onSpeechStart: () => {
          if (!socket) return;
          // Talking over Aethel: stop at once and say what was heard, before saying that speech began. The turn is
          // the open one, or (once the server has finished it) the reply still playing; an acknowledgement or a
          // narration line (id -1) belongs to no reply, so it is just stopped.
          const heard = playback?.lastStarted ?? null;
          const replyHeard = heard && heard.sentenceId >= 0 ? heard : null;
          const turn = activeTurn ?? (playback?.playing ? replyHeard?.turnId ?? null : null);
          if (turn) {
            const last = replyHeard && replyHeard.turnId === turn ? replyHeard.sentenceId : null;
            socket.send({ type: "interrupt", last_started_sentence_id: last, turn_id: turn });
            dropped.add(turn);
          }
          playback?.stop();
          activeTurn = null;
          metas = new Map();
          socket.send({ type: "speech_start" });
          set({ state: "hearing", speaking: "" });
        },
        onSpeechEnd: (pcm, durationMs) => {
          if (!socket) return;
          socket.send({ type: "speech_end", duration_ms: Math.min(60000, durationMs), misfire: false });
          socket.sendAudio(pcm);
          set({ state: "thinking" });
        },
        onLevel: (rms) => set({ inputLevel: rms }),
      });
      if (mine !== generation) return void c.stop(); // the call ended while the microphone was starting
      capture = c;
      c.setMuted(get().muted);
      set({ state: "listening" });
    } catch (e) {
      if (mine === generation) teardown("error", e instanceof Error && e.message ? `The microphone isn't available: ${e.message}` : "The microphone isn't available.");
    }
  };

  return {
    state: "idle",
    conversationId: null,
    personaName: "Aethel",
    muted: false,
    captions: [],
    speaking: "",
    error: null,
    inputLevel: 0,
    outputLevel: () => playback?.level() ?? 0,

    async startCall(conversationId) {
      if (get().state === "connecting") return; // a double click
      if (socket) await get().endCall(); // one call at a time
      generation += 1;
      const mine = generation;
      set({ state: "connecting", conversationId, captions: [], speaking: "", error: null });
      try {
        const p = deps.playback();
        p.onIdle = settle;
        p.onStarted = (meta) => {
          const text = (meta as SentenceMeta & { text?: string }).text ?? "";
          if (text) set({ speaking: text });
        };
        playback = p;
        const s = await deps.open({
          onMessage: (m) => { if (mine === generation) onMessage(m); },
          onAudio: (id, a) => { if (mine === generation) onAudio(id, a); },
          onClose: () => { if (mine === generation) teardown(get().state === "error" ? "error" : "ended", get().error); },
        });
        if (mine !== generation) return s.close();
        socket = s;
        s.send({ type: "hello", conversation_id: conversationId });
      } catch (e) {
        if (mine === generation) teardown("error", e instanceof Error ? e.message : "Couldn't start the call.");
      }
    },

    async endCall() {
      socket?.send({ type: "end_call" });
      teardown("ended");
    },

    toggleMute() {
      const muted = !get().muted;
      capture?.setMuted(muted);
      if (muted && get().state === "hearing" && socket) {
        // Muted mid-sentence: that speech will never end on its own, so end it here (it is not sent).
        socket.send({ type: "speech_end", duration_ms: 0, misfire: true });
        set({ muted, state: "listening" });
        return;
      }
      set({ muted });
    },
  };
});
