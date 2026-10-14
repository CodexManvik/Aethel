/** Plays a reply's sentences back to back, as they arrive, and knows which one the user heard begin. */
export interface SentenceMeta { sentenceId: number; turnId: string; sampleRate: number }

interface Scheduled { meta: SentenceMeta; source: AudioBufferSourceNode; startAt: number; ended: boolean }

export function pcm16ToFloat(audio: ArrayBuffer): Float32Array<ArrayBuffer> {
  const ints = new Int16Array(audio, 0, Math.floor(audio.byteLength / 2));
  const out = new Float32Array(ints.length);
  for (let i = 0; i < ints.length; i++) out[i] = ints[i] / 32768;
  return out;
}

export class Playback {
  readonly ctx: AudioContext;
  readonly analyser: AnalyserNode;
  onIdle?: () => void;
  onStarted?: (meta: SentenceMeta) => void; // a sentence began to play (for the captions)
  private items: Scheduled[] = [];
  private timers: ReturnType<typeof setTimeout>[] = [];
  private nextStart = 0;

  constructor(ctx: AudioContext = new AudioContext()) {
    this.ctx = ctx;
    this.analyser = ctx.createAnalyser();
    this.analyser.fftSize = 512;
    this.analyser.connect(ctx.destination);
  }

  enqueue(meta: SentenceMeta, audio: ArrayBuffer): void {
    const samples = pcm16ToFloat(audio);
    if (!samples.length) return;
    const buffer = this.ctx.createBuffer(1, samples.length, meta.sampleRate);
    buffer.copyToChannel(samples, 0);
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.analyser);
    const startAt = Math.max(this.ctx.currentTime, this.nextStart);
    this.nextStart = startAt + buffer.duration;
    const item: Scheduled = { meta, source, startAt, ended: false };
    source.onended = () => {
      item.ended = true;
      if (this.items.length && this.items.every((i) => i.ended)) {
        this.items = [];
        this.onIdle?.();
      }
    };
    this.items.push(item);
    void this.ctx.resume?.();
    source.start(startAt);
    this.timers.push(setTimeout(() => this.onStarted?.(meta), Math.max(0, (startAt - this.ctx.currentTime) * 1000)));
  }

  /** Stop now and drop everything queued. */
  stop(): void {
    const items = this.items;
    this.items = [];
    this.nextStart = 0;
    this.timers.forEach(clearTimeout);
    this.timers = [];
    for (const item of items) {
      item.source.onended = null;
      try { item.source.stop(); } catch { /* not started */ }
    }
  }

  /** The last sentence whose playback had begun (null: none yet), with its turn. */
  get lastStarted(): SentenceMeta | null {
    const now = this.ctx.currentTime;
    let last: SentenceMeta | null = null;
    for (const item of this.items) if (item.startAt <= now) last = item.meta;
    return last;
  }

  get playing(): boolean {
    return this.items.some((i) => !i.ended);
  }

  /** Output level 0..1, for the portrait's rings. */
  level(): number {
    const data = new Float32Array(this.analyser.fftSize);
    this.analyser.getFloatTimeDomainData(data);
    let sum = 0;
    for (const s of data) sum += s * s;
    return Math.sqrt(sum / data.length);
  }

  close(): void {
    this.stop();
    void this.ctx.close?.();
  }
}
