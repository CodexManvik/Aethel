/** The microphone, cut into utterances by Silero VAD (running in the webview). Echo cancellation keeps Aethel's own
 * voice out of the microphone, so the user can talk over it. */
export interface Capture {
  stop(): Promise<void>;
  setMuted(muted: boolean): void;
}

export interface CaptureCallbacks {
  onSpeechStart(): void;                                     // real speech (not a click or a cough) has begun
  onSpeechEnd(pcm16: ArrayBuffer, durationMs: number): void; // 16 kHz mono
  onLevel(rms: number): void;                                // input level, for the visuals
}

export const VAD_OPTIONS = {
  model: "v5" as const,
  baseAssetPath: "/vad/",
  onnxWASMBasePath: "/vad/",
  positiveSpeechThreshold: 0.6,
  negativeSpeechThreshold: 0.45,
  redemptionMs: 250, // this much quiet ends an utterance (the server decides whether the turn is over)
  minSpeechMs: 130,  // shorter than this is a noise
  preSpeechPadMs: 300,
};

export function floatTo16(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    out[i] = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767);
  }
  return out;
}

export function rms(frame: Float32Array): number {
  if (!frame.length) return 0;
  let sum = 0;
  for (const s of frame) sum += s * s;
  return Math.sqrt(sum / frame.length);
}

interface Vad { start(): Promise<void> | void; destroy(): Promise<void> | void }
type VadModule = { MicVAD: { new: (opts: Record<string, unknown>) => Promise<Vad> } };

export async function startCapture(
  cb: CaptureCallbacks,
  load: () => Promise<VadModule> = () => import("@ricky0123/vad-web") as unknown as Promise<VadModule>,
): Promise<Capture> {
  let muted = false;
  const { MicVAD } = await load();
  const vad = await MicVAD.new({
    ...VAD_OPTIONS,
    startOnLoad: false, // started below, once it's ours to stop
    // No cross-origin isolation in the webview, so no threads for onnxruntime.
    ortConfig: (ort: { env: { wasm: { numThreads: number } } }) => { ort.env.wasm.numThreads = 1; },
    getStream: () => navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    }),
    onSpeechRealStart: () => { if (!muted) cb.onSpeechStart(); },
    onSpeechEnd: (audio: Float32Array) => {
      if (!muted) cb.onSpeechEnd(floatTo16(audio).buffer as ArrayBuffer, Math.round(audio.length / 16));
    },
    onFrameProcessed: (_p: unknown, frame: Float32Array) => cb.onLevel(rms(frame)),
  });
  try {
    await vad.start();
  } catch (e) {
    await vad.destroy(); // e.g. the microphone was refused: release the model too
    throw e;
  }
  return {
    stop: async () => { await vad.destroy(); },
    setMuted: (m) => { muted = m; },
  };
}
