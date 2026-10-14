import { floatTo16, rms, startCapture, VAD_OPTIONS } from "./capture";
import { Playback, pcm16ToFloat } from "./playback";
// @ts-expect-error a plain .mjs script with no types
import { VAD_ASSETS } from "../../../../scripts/copy-vad-assets.mjs";

class FakeSource {
  buffer: { duration: number } | null = null;
  onended: (() => void) | null = null;
  started: number | null = null;
  stopped = false;
  connect() {}
  start(at: number) { this.started = at; }
  stop() { this.stopped = true; }
}

class FakeCtx {
  currentTime = 0;
  destination = {};
  sources: FakeSource[] = [];
  createAnalyser() { return { fftSize: 0, connect() {}, getFloatTimeDomainData(d: Float32Array) { d.fill(0.5); } }; }
  createBuffer(_c: number, length: number, rate: number) { return { duration: length / rate, copyToChannel: () => {} }; }
  createBufferSource() { const s = new FakeSource(); this.sources.push(s); return s; }
  resume() { return Promise.resolve(); }
}

const pcm = (samples: number) => new Int16Array(samples).fill(1000).buffer;
const meta = (sentenceId: number, turnId = "vt_1") => ({ sentenceId, turnId, sampleRate: 1000 });
const playback = () => { const ctx = new FakeCtx(); return { ctx, p: new Playback(ctx as unknown as AudioContext) }; };

test("floatTo16 clamps and scales", () => {
  expect(Array.from(floatTo16(new Float32Array([0, 1, -1, 2, -2, 0.5])))).toEqual([0, 32767, -32768, 32767, -32768, 16384]);
  expect(Array.from(pcm16ToFloat(new Int16Array([0, -32768, 16384]).buffer))).toEqual([0, -1, 0.5]);
  expect(rms(new Float32Array([0.5, -0.5]))).toBeCloseTo(0.5);
  expect(rms(new Float32Array())).toBe(0);
});

test("sentences play back to back in order", () => {
  const { ctx, p } = playback();
  p.enqueue(meta(0), pcm(500)); // 0.5 s at 1 kHz
  p.enqueue(meta(1), pcm(250));
  expect(ctx.sources.map((s) => s.started)).toEqual([0, 0.5]);
  expect(p.playing).toBe(true);
  expect(p.lastStarted?.sentenceId).toBe(0);
  ctx.currentTime = 0.6;
  expect(p.lastStarted).toEqual(meta(1));
});

test("stop empties the queue, keeps what was heard, and the next turn starts from now", () => {
  const { ctx, p } = playback();
  p.enqueue(meta(0), pcm(500));
  p.enqueue(meta(1), pcm(500));
  ctx.currentTime = 0.2;
  const heard = p.lastStarted;
  p.stop();
  expect(heard?.sentenceId).toBe(0);
  expect(ctx.sources.every((s) => s.stopped)).toBe(true);
  expect(p.playing).toBe(false);
  p.enqueue(meta(0, "vt_2"), pcm(100));
  expect(ctx.sources[2].started).toBe(0.2);
});

test("onIdle fires when the last sentence ends, and not after a stop", () => {
  const { ctx, p } = playback();
  const idle = vi.fn();
  p.onIdle = idle;
  p.enqueue(meta(0), pcm(100));
  p.enqueue(meta(1), pcm(100));
  ctx.sources[0].onended?.();
  expect(idle).not.toHaveBeenCalled();
  ctx.sources[1].onended?.();
  expect(idle).toHaveBeenCalledTimes(1);
  expect(p.lastStarted?.sentenceId).toBe(1); // still known while the next sentence is on its way
  p.enqueue(meta(2), pcm(100));
  p.stop();
  expect(idle).toHaveBeenCalledTimes(1);
  expect(p.level()).toBeCloseTo(0.5);
});

test("capture passes the VAD settings, and muting silences speech but not the level", async () => {
  let opts: Record<string, unknown> = {};
  const vad = { start: vi.fn(), destroy: vi.fn() };
  const cb = { onSpeechStart: vi.fn(), onSpeechEnd: vi.fn(), onLevel: vi.fn() };
  const capture = await startCapture(cb, async () => ({ MicVAD: { new: async (o) => { opts = o; return vad; } } }));
  const call = (name: string, ...args: unknown[]) => (opts[name] as (...a: unknown[]) => void)(...args);
  expect(opts.baseAssetPath).toBe("/vad/");
  expect(opts.redemptionMs).toBe(VAD_OPTIONS.redemptionMs);
  const ort = { env: { wasm: { numThreads: 4 } } };
  call("ortConfig", ort);
  expect(ort.env.wasm.numThreads).toBe(1);
  call("onSpeechRealStart");
  call("onSpeechEnd", new Float32Array(1600));
  expect(cb.onSpeechStart).toHaveBeenCalled();
  expect(cb.onSpeechEnd).toHaveBeenCalledWith(expect.any(ArrayBuffer), 100);
  capture.setMuted(true);
  call("onSpeechRealStart");
  call("onFrameProcessed", {}, new Float32Array([0.5]));
  expect(cb.onSpeechStart).toHaveBeenCalledTimes(1);
  expect(cb.onLevel).toHaveBeenCalledWith(0.5);
  await capture.stop();
  expect(vad.destroy).toHaveBeenCalled();
});

test("the asset script copies the model, the worklet and onnxruntime's wasm", () => {
  expect(VAD_ASSETS.map(([, name]: [string, string]) => name)).toEqual(
    ["silero_vad_v5.onnx", "vad.worklet.bundle.min.js", "ort-wasm-simd-threaded.wasm", "ort-wasm-simd-threaded.mjs"]);
});
