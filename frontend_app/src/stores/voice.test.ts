import type { CaptureCallbacks } from "../features/call/audio/capture";
import type { Playback, SentenceMeta } from "../features/call/audio/playback";
import type { VoiceHandlers } from "../features/call/voiceSocket";
import { setVoiceDeps, useVoice } from "./voice";

let sent: unknown[];
let handlers: VoiceHandlers;
let mic: CaptureCallbacks;
let muted: boolean;
let player: FakePlayback;

class FakePlayback {
  queue: SentenceMeta[] = [];
  started: SentenceMeta | null = null;
  playing = false;
  onIdle?: () => void;
  onStarted?: (m: SentenceMeta) => void;
  enqueue(meta: SentenceMeta) { this.queue.push(meta); this.playing = true; }
  stop() { this.queue = []; this.playing = false; }
  get lastStarted() { return this.started; }
  level() { return 0.3; }
  close() { this.stop(); }
}

const frame = (id: number) => new ArrayBuffer(8);
const server = (msg: object) => handlers.onMessage(msg as never);
const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(async () => {
  sent = [];
  muted = false;
  player = new FakePlayback();
  setVoiceDeps({
    open: async (h) => { handlers = h; return { send: (m) => sent.push(m), sendAudio: (a) => sent.push({ audio: a.byteLength }), close: () => {} }; },
    capture: async (cb) => { mic = cb; return { stop: async () => {}, setMuted: (m) => { muted = m; } }; },
    playback: () => player as unknown as Playback,
  });
  await useVoice.getState().endCall();
  useVoice.setState({ state: "idle", captions: [], error: null, muted: false });
});

async function inCall() {
  await useVoice.getState().startCall("conv_1");
  server({ type: "ready", stt: "azure:uksouth", tts_voice: "af_heart", persona_name: "Aethel" });
  await flush();
}

test("a whole turn: hello, listening, hearing, thinking, speaking, listening", async () => {
  await useVoice.getState().startCall("conv_1");
  expect(sent[0]).toEqual({ type: "hello", conversation_id: "conv_1" });
  expect(useVoice.getState().state).toBe("connecting");
  server({ type: "ready", stt: "azure:uksouth", tts_voice: "af_heart", persona_name: "Aethel" });
  await flush();
  expect(useVoice.getState().state).toBe("listening");
  mic.onSpeechStart();
  expect(useVoice.getState().state).toBe("hearing");
  mic.onSpeechEnd(new ArrayBuffer(3200), 100);
  expect(sent.slice(1)).toEqual([{ type: "speech_start" }, { type: "speech_end", duration_ms: 100, misfire: false }, { audio: 3200 }]);
  expect(useVoice.getState().state).toBe("thinking");
  server({ type: "caption", role: "user", text: "hello", final: true, message_id: null });
  server({ type: "thinking" });
  server({ type: "tts_meta", sentence_id: 0, text: "Hi there.", codec: "pcm16", sample_rate: 24000, turn_id: "vt_1" });
  handlers.onAudio(0, frame(0));
  expect(useVoice.getState().state).toBe("speaking");
  expect(player.queue[0]).toMatchObject({ sentenceId: 0, turnId: "vt_1", sampleRate: 24000 });
  player.onStarted?.(player.queue[0]);
  expect(useVoice.getState().speaking).toBe("Hi there.");
  server({ type: "turn_end", turn_id: "vt_1", status: "complete" });
  expect(useVoice.getState().state).toBe("speaking"); // still playing
  player.playing = false;
  player.onIdle?.();
  expect(useVoice.getState().state).toBe("listening");
  expect(useVoice.getState().captions).toEqual([{ role: "user", text: "hello", final: true }]);
});

test("barge-in stops playback and sends interrupt, naming the sentence and turn, before speech_start", async () => {
  await inCall();
  server({ type: "tts_meta", sentence_id: 0, text: "One.", codec: "pcm16", sample_rate: 24000, turn_id: "vt_9" });
  handlers.onAudio(0, frame(0));
  player.started = player.queue[0];
  sent = [];
  mic.onSpeechStart();
  expect(sent).toEqual([{ type: "interrupt", last_started_sentence_id: 0, turn_id: "vt_9" }, { type: "speech_start" }]);
  expect(player.playing).toBe(false);
  expect(useVoice.getState().state).toBe("hearing");
});

test("speaking while a reply is coming but nothing has played yet interrupts with no sentence heard", async () => {
  await inCall();
  server({ type: "thinking", turn_id: "vt_2" });
  server({ type: "tts_meta", sentence_id: 0, text: "One.", codec: "pcm16", sample_rate: 24000, turn_id: "vt_2" });
  sent = [];
  mic.onSpeechStart();
  expect(sent[0]).toEqual({ type: "interrupt", last_started_sentence_id: null, turn_id: "vt_2" });
});

test("speaking between turns sends no interrupt", async () => {
  await inCall();
  sent = [];
  mic.onSpeechStart();
  expect(sent).toEqual([{ type: "speech_start" }]);
});

test("mute passes through to the microphone", async () => {
  await inCall();
  useVoice.getState().toggleMute();
  expect(muted).toBe(true);
  expect(useVoice.getState().muted).toBe(true);
});

test("the captions keep the last six", async () => {
  await inCall();
  for (let i = 0; i < 9; i++) server({ type: "caption", role: "user", text: `line ${i}`, final: true, message_id: null });
  expect(useVoice.getState().captions.map((c) => c.text)).toEqual(["line 3", "line 4", "line 5", "line 6", "line 7", "line 8"]);
});

test("the socket closing ends the call, and a fatal error says why", async () => {
  await inCall();
  handlers.onClose();
  expect(useVoice.getState().state).toBe("ended");
  await inCall();
  server({ type: "voice_error", message: "This conversation is already in a call.", fatal: true });
  expect(useVoice.getState()).toMatchObject({ state: "error", error: "This conversation is already in a call." });
});

test("starting a call in another conversation ends the first, and events from the old call are ignored", async () => {
  await inCall();
  const old = handlers;
  await useVoice.getState().startCall("conv_2");
  expect(sent).toContainEqual({ type: "end_call" });
  expect(useVoice.getState().conversationId).toBe("conv_2");
  old.onMessage({ type: "thinking" } as never);
  expect(useVoice.getState().state).toBe("connecting");
});

test("a microphone that can't start ends the call with a plain message", async () => {
  setVoiceDeps({ capture: async () => { throw new Error("Permission denied"); } });
  await inCall();
  expect(useVoice.getState()).toMatchObject({ state: "error", error: "The microphone isn't available: Permission denied" });
});

test("speaking while Aethel is still thinking interrupts that turn, and its late audio is not played", async () => {
  await inCall();
  mic.onSpeechEnd(new ArrayBuffer(10), 1);
  server({ type: "thinking", turn_id: "vt_5" });
  sent = [];
  mic.onSpeechStart();
  expect(sent[0]).toEqual({ type: "interrupt", last_started_sentence_id: null, turn_id: "vt_5" });
  server({ type: "tts_meta", sentence_id: 0, text: "Late.", codec: "pcm16", sample_rate: 24000, turn_id: "vt_5" });
  handlers.onAudio(0, frame(0));
  expect(player.queue).toEqual([]);
  expect(useVoice.getState().state).toBe("hearing");
});

test("a pause between sentences still counts what was heard", async () => {
  await inCall();
  server({ type: "thinking", turn_id: "vt_6" });
  server({ type: "tts_meta", sentence_id: 0, text: "One.", codec: "pcm16", sample_rate: 24000, turn_id: "vt_6" });
  handlers.onAudio(0, frame(0));
  player.started = player.queue[0];
  player.playing = false; // sentence 0 is over, sentence 1 hasn't arrived
  sent = [];
  mic.onSpeechStart();
  expect(sent[0]).toEqual({ type: "interrupt", last_started_sentence_id: 0, turn_id: "vt_6" });
});

test("barge-in over an acknowledgement interrupts the open turn with nothing heard", async () => {
  await inCall();
  server({ type: "thinking", turn_id: "vt_7" });
  server({ type: "tts_meta", sentence_id: -1, text: "Mm.", codec: "pcm16", sample_rate: 24000, turn_id: "vt_7" });
  handlers.onAudio(-1, frame(-1));
  player.started = player.queue[0];
  sent = [];
  mic.onSpeechStart();
  expect(sent[0]).toEqual({ type: "interrupt", last_started_sentence_id: null, turn_id: "vt_7" });
});

test("an utterance with no words, or a recognition error, goes back to listening", async () => {
  await inCall();
  mic.onSpeechEnd(new ArrayBuffer(10), 1);
  expect(useVoice.getState().state).toBe("thinking");
  server({ type: "no_speech" });
  expect(useVoice.getState().state).toBe("listening");
  mic.onSpeechEnd(new ArrayBuffer(10), 1);
  server({ type: "voice_error", message: "Speech recognition failed.", fatal: false });
  expect(useVoice.getState()).toMatchObject({ state: "listening", error: "Speech recognition failed." });
});

test("muting mid-sentence ends that speech as a misfire", async () => {
  await inCall();
  mic.onSpeechStart();
  sent = [];
  useVoice.getState().toggleMute();
  expect(sent).toEqual([{ type: "speech_end", duration_ms: 0, misfire: true }]);
  expect(useVoice.getState().state).toBe("listening");
});
