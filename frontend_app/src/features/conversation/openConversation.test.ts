import { beforeEach, expect, test, vi } from "vitest";
import { useSession } from "../../stores/session";

const answers = vi.hoisted(() => new Map<string, (v: unknown) => void>());
vi.mock("../../lib/api", () => ({
  api: (path: string) => (path.includes("/messages") ? new Promise((resolve) => answers.set(path, resolve)) : Promise.resolve([])),
}));

import { openConversation } from "./useConversations";

beforeEach(() => answers.clear());

test("a slow answer for an earlier click doesn't replace the conversation asked for last", async () => {
  const first = openConversation("a");
  const second = openConversation("b");
  answers.get("/api/conversations/b/messages")!([]);
  await second;
  answers.get("/api/conversations/a/messages")!([]);
  await first;
  expect(useSession.getState().conversationId).toBe("b");
});
