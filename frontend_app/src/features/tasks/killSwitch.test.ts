import { vi } from "vitest";
import { setSocketForTests } from "../../lib/session";

const handlers: Record<string, () => void> = {};
vi.mock("../../lib/backend", () => ({ inTauri: () => true }));
vi.mock("@tauri-apps/api/event", () => ({
  listen: async (name: string, cb: () => void) => {
    handlers[name] = cb;
    return () => delete handlers[name];
  },
}));

test("the desktop kill-switch hotkey cancels every task over the socket", async () => {
  const sent: unknown[] = [];
  setSocketForTests({ send: (e) => sent.push(e), subscribe: () => () => {}, onStatus: () => () => {} });
  const { listenForKillSwitch } = await import("./taskActions");
  const unlisten = await listenForKillSwitch();
  handlers["kill-switch"]();
  expect(sent).toEqual([{ type: "kill_switch" }]);
  unlisten();
  expect(handlers["kill-switch"]).toBeUndefined();
});
