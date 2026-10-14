import { getBackendInfo } from "../../lib/backend";
import type { Client, Server } from "../../lib/voice.gen";

/** /ws/voice: JSON messages both ways, plus binary audio (see backend/aethel/voice/protocol.py). */
export interface VoiceTransport {
  send(msg: Client): void;
  sendAudio(pcm16: ArrayBuffer): void;
  close(): void;
}

export interface VoiceHandlers {
  onMessage(msg: Server): void;
  onAudio(sentenceId: number, audio: ArrayBuffer): void; // a frame is a 4-byte little-endian id, then the audio
  onClose(): void;
}

export async function voiceUrl(): Promise<string> {
  const { url, token } = await getBackendInfo();
  const ws = new URL("/ws/voice", url.replace(/^http/, "ws"));
  if (token) ws.searchParams.set("token", token);
  return ws.toString();
}

export function openVoiceSocket(url: string, h: VoiceHandlers, WS: typeof WebSocket = WebSocket): Promise<VoiceTransport> {
  return new Promise((resolve, reject) => {
    const ws = new WS(url);
    ws.binaryType = "arraybuffer";
    let opened = false;
    ws.onopen = () => {
      opened = true;
      resolve({
        send: (msg) => { if (ws.readyState === WS.OPEN) ws.send(JSON.stringify(msg)); },
        sendAudio: (pcm) => { if (ws.readyState === WS.OPEN) ws.send(pcm); },
        close: () => ws.close(),
      });
    };
    ws.onmessage = (ev: MessageEvent) => {
      if (typeof ev.data === "string") {
        try { h.onMessage(JSON.parse(ev.data) as Server); } catch { /* not a message we know */ }
      } else if (ev.data instanceof ArrayBuffer && ev.data.byteLength >= 4) {
        h.onAudio(new DataView(ev.data).getInt32(0, true), ev.data.slice(4));
      }
    };
    ws.onclose = () => { if (opened) h.onClose(); else reject(new Error("Couldn't connect the call.")); };
  });
}
