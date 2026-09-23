import { SessionSocket } from "./ws";

class FakeWS {
  static instances: FakeWS[] = [];
  static OPEN = 1;
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) {
    FakeWS.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.onclose?.();
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
}

beforeEach(() => {
  FakeWS.instances = [];
  vi.useFakeTimers();
});
afterEach(() => vi.useRealTimers());

const makeSocket = () =>
  new SessionSocket(async () => "ws://x/ws/session", FakeWS as unknown as typeof WebSocket);

test("queues sends until open, then flushes in order", async () => {
  const s = makeSocket();
  await s.connect();
  s.send({ type: "stop_generation", message_id: "a" });
  s.send({ type: "stop_generation", message_id: "b" });
  const ws = FakeWS.instances[0];
  expect(ws.sent).toEqual([]);
  ws.open();
  expect(ws.sent.map((d) => JSON.parse(d).message_id)).toEqual(["a", "b"]);
});

test("dispatches parsed server events and reports status", async () => {
  const s = makeSocket();
  const events: unknown[] = [];
  const statuses: string[] = [];
  s.subscribe((e) => events.push(e));
  s.onStatus((st) => statuses.push(st));
  await s.connect();
  const ws = FakeWS.instances[0];
  ws.open();
  ws.onmessage?.({ data: JSON.stringify({ type: "token", message_id: "m", text: "hi" }) });
  ws.onmessage?.({ data: "not json" });
  expect(events).toEqual([{ type: "token", message_id: "m", text: "hi" }]);
  expect(statuses).toEqual(["connecting", "open"]);
});

test("reconnects with backoff after an unexpected close, not after close()", async () => {
  const s = makeSocket();
  await s.connect();
  FakeWS.instances[0].open();
  FakeWS.instances[0].onclose?.();
  await vi.advanceTimersByTimeAsync(1000);
  expect(FakeWS.instances).toHaveLength(2);
  s.close();
  await vi.advanceTimersByTimeAsync(20000);
  expect(FakeWS.instances).toHaveLength(2);
});
