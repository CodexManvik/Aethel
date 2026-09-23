import { SessionSocket, type SessionTransport } from "./ws";

let transport: SessionTransport | null = null;

export function getSocket(): SessionTransport {
  if (!transport) {
    const socket = new SessionSocket();
    void socket.connect();
    transport = socket;
  }
  return transport;
}

export function setSocketForTests(t: SessionTransport | null) {
  transport = t;
}
