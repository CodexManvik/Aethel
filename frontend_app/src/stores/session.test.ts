import { applyEvent, toUiMessages, type SessionData } from "./session";

const base = (): SessionData => ({
  conversationId: "c1",
  messages: [{ id: "tmp", role: "user", content: "hi", status: "pending", clientId: "k1" }],
  streamingId: null,
  notices: [],
  socketStatus: "open",
});

test("message_start confirms the pending user message and opens an assistant bubble", () => {
  const next = applyEvent(base(), {
    type: "message_start", conversation_id: "c1", message_id: "a1", user_message_id: "u1", client_id: "k1", role: "assistant",
  });
  expect(next.messages).toEqual([
    { id: "u1", role: "user", content: "hi", status: "complete", clientId: "k1" },
    { id: "a1", role: "assistant", content: "", status: "streaming" },
  ]);
  expect(next.streamingId).toBe("a1");
});

test("tokens append, message_end settles", () => {
  let s = applyEvent(base(), {
    type: "message_start", conversation_id: "c1", message_id: "a1", user_message_id: "u1", client_id: "k1", role: "assistant",
  });
  s = applyEvent(s, { type: "token", message_id: "a1", text: "Hel" });
  s = applyEvent(s, { type: "token", message_id: "a1", text: "lo" });
  s = applyEvent(s, { type: "message_end", message_id: "a1", status: "complete" });
  expect(s.messages[1]).toMatchObject({ content: "Hello", status: "complete" });
  expect(s.streamingId).toBeNull();
});

test("events for another conversation are ignored", () => {
  const s = applyEvent(base(), {
    type: "message_start", conversation_id: "other", message_id: "a1", user_message_id: "u1", client_id: null, role: "assistant",
  });
  expect(s).toEqual(base());
  expect(applyEvent(base(), { type: "token", message_id: "zzz", text: "x" })).toEqual(base());
});

test("errors attach to their message or become notices", () => {
  let s = applyEvent(base(), {
    type: "message_start", conversation_id: "c1", message_id: "a1", user_message_id: "u1", client_id: "k1", role: "assistant",
  });
  s = applyEvent(s, { type: "error", code: "no_provider", message: "groq:x: no API key", message_id: "a1" });
  expect(s.messages[1]).toMatchObject({ status: "error", error: "groq:x: no API key", errorCode: "no_provider" });
  s = applyEvent(s, { type: "error", code: "bad_request", message: "Invalid event.", message_id: null });
  expect(s.notices.map((n) => n.text)).toEqual(["Invalid event."]);
});

test("provider switches become gentle notices", () => {
  const s = applyEvent(base(), {
    type: "provider_switched", role: "chat", from_provider: "groq:a", to_provider: "openrouter:b", reason: "429",
    message_id: null, task_id: null,
  });
  expect(s.notices[0].text).toBe("groq:a was unavailable, so openrouter:b answered instead.");
});

test("provider_switched for another conversation's message is ignored", () => {
  const s = applyEvent(base(), {
    type: "provider_switched", role: "chat", from_provider: "a", to_provider: "b", reason: "429",
    message_id: "not-here", task_id: null,
  });
  expect(s.notices).toEqual([]);
});

test("an unscoped error fails the pending user message instead of leaving it stuck", () => {
  const s = applyEvent(base(), { type: "error", code: "bad_request", message: "Conversation not found.", message_id: null });
  expect(s.messages[0]).toMatchObject({ status: "error", error: "Conversation not found." });
  expect(s.notices.map((n) => n.text)).toEqual(["Conversation not found."]);
});

test("replaceMessages keeps local pending messages the server doesn't know yet", async () => {
  const { useSession } = await import("./session");
  useSession.setState({ conversationId: "c1", messages: [
    { id: "pending_k2", role: "user", content: "second", status: "pending", clientId: "k2" },
  ], streamingId: null, notices: [], socketStatus: "open" });
  useSession.getState().replaceMessages("c1", [{ id: "u1", role: "user", content: "first", status: "complete" }]);
  expect(useSession.getState().messages.map((m) => m.content)).toEqual(["first", "second"]);
});

test("toUiMessages drops system messages", () => {
  expect(
    toUiMessages([
      { id: "s", conversation_id: "c", role: "system", content: "x", status: "complete", meta: {}, created_at: "" },
      { id: "u", conversation_id: "c", role: "user", content: "hi", status: "complete", meta: {}, created_at: "" },
    ]),
  ).toEqual([{ id: "u", role: "user", content: "hi", status: "complete" }]);
});
