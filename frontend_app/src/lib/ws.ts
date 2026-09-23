import { getBackendInfo } from "./backend";
import type { ClientEvent, ServerEvent } from "./events";

export type SocketStatus = "connecting" | "open" | "closed";

export interface SessionTransport {
  send(event: ClientEvent): void;
  subscribe(listener: (event: ServerEvent) => void): () => void;
  onStatus(listener: (status: SocketStatus) => void): () => void;
}

export async function sessionUrl(): Promise<string> {
  const { url, token } = await getBackendInfo();
  const ws = new URL("/ws/session", url.replace(/^http/, "ws"));
  if (token) ws.searchParams.set("token", token);
  return ws.toString();
}

export class SessionSocket implements SessionTransport {
  private ws: WebSocket | null = null;
  private listeners = new Set<(event: ServerEvent) => void>();
  private statusListeners = new Set<(status: SocketStatus) => void>();
  private queue: ClientEvent[] = [];
  private retries = 0;
  private stopped = false;

  constructor(
    private makeUrl: () => Promise<string> = sessionUrl,
    private WS: typeof WebSocket = WebSocket,
  ) {}

  async connect(): Promise<void> {
    this.stopped = false;
    this.setStatus("connecting");
    const ws = new this.WS(await this.makeUrl());
    this.ws = ws;
    ws.onopen = () => {
      this.retries = 0;
      this.setStatus("open");
      const pending = this.queue.splice(0);
      pending.forEach((ev) => ws.send(JSON.stringify(ev)));
    };
    ws.onmessage = (msg: MessageEvent | { data: string }) => {
      let event: ServerEvent;
      try {
        event = JSON.parse(String(msg.data)) as ServerEvent;
      } catch {
        return;
      }
      this.listeners.forEach((l) => l(event));
    };
    ws.onclose = () => {
      this.ws = null;
      this.setStatus("closed");
      if (this.stopped) return;
      const delay = Math.min(1000 * 2 ** this.retries, 10000);
      this.retries += 1;
      setTimeout(() => {
        if (!this.stopped) void this.connect();
      }, delay);
    };
  }

  send(event: ClientEvent): void {
    if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(event));
    else this.queue.push(event);
  }

  subscribe(listener: (event: ServerEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onStatus(listener: (status: SocketStatus) => void): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  close(): void {
    this.stopped = true;
    this.ws?.close();
  }

  private setStatus(status: SocketStatus) {
    this.statusListeners.forEach((l) => l(status));
  }
}
