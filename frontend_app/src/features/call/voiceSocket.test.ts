import { openVoiceSocket } from "./voiceSocket";

class FakeWS {
  static OPEN = 1;
  static last: FakeWS;
  readyState = 1;
  binaryType = "";
  sent: unknown[] = [];
  onopen?: () => void;
  onmessage?: (ev: { data: unknown }) => void;
  onclose?: () => void;
  constructor(public url: string) { FakeWS.last = this; }
  send(d: unknown) { this.sent.push(d); }
  close() { this.onclose?.(); }
}

test("JSON goes out as text, audio as binary; a frame's first 4 bytes are the sentence id", async () => {
  const got: unknown[] = [];
  const audio: [number, number][] = [];
  const closed = vi.fn();
  const opening = openVoiceSocket("ws://x/ws/voice", { onMessage: (m) => got.push(m), onAudio: (id, a) => audio.push([id, a.byteLength]), onClose: closed },
    FakeWS as unknown as typeof WebSocket);
  const ws = FakeWS.last;
  ws.onopen?.();
  const t = await opening;
  expect(ws.binaryType).toBe("arraybuffer");
  t.send({ type: "speech_start" });
  t.sendAudio(new ArrayBuffer(4));
  expect(ws.sent[0]).toBe('{"type":"speech_start"}');
  expect(ws.sent[1]).toBeInstanceOf(ArrayBuffer);
  ws.onmessage?.({ data: '{"type":"thinking"}' });
  ws.onmessage?.({ data: "not json" });
  const frame = new ArrayBuffer(10);
  new DataView(frame).setInt32(0, -1, true);
  ws.onmessage?.({ data: frame });
  ws.onmessage?.({ data: new ArrayBuffer(2) }); // too short to be a frame
  expect(got).toEqual([{ type: "thinking" }]);
  expect(audio).toEqual([[-1, 6]]);
  t.close();
  expect(closed).toHaveBeenCalled();
});

test("a socket that closes before opening rejects", async () => {
  const opening = openVoiceSocket("ws://x", { onMessage: () => {}, onAudio: () => {}, onClose: () => {} }, FakeWS as unknown as typeof WebSocket);
  FakeWS.last.onclose?.();
  await expect(opening).rejects.toThrow("Couldn't connect the call.");
});
