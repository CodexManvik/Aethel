import { applyEvent, toUiMessages, type SessionData } from "./session";

const base = (): SessionData => ({
  conversationId: "c1",
  messages: [{ id: "tmp", role: "user", content: "hi", status: "pending", clientId: "k1" }],
  streamingId: null,
  notices: [],
  socketStatus: "open",
  pendingSources: {},
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
  s = applyEvent(s, { type: "error", code: "no_provider", message: "groq:x: no API key", message_id: "a1", conversation_id: null });
  expect(s.messages[1]).toMatchObject({ status: "error", error: "groq:x: no API key", errorCode: "no_provider" });
  s = applyEvent(s, { type: "error", code: "bad_request", message: "Invalid event.", message_id: null, conversation_id: null });
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
  const s = applyEvent(base(), {
    type: "error", code: "bad_request", message: "Conversation not found.", message_id: null, conversation_id: "c1",
  });
  expect(s.messages[0]).toMatchObject({ status: "error", error: "Conversation not found." });
  expect(s.notices.map((n) => n.text)).toEqual(["Conversation not found."]);
});

test("an error scoped to another window's conversation leaves this conversation's pending message alone", () => {
  const s = applyEvent(base(), {
    type: "error", code: "bad_request", message: "Conversation not found.", message_id: null, conversation_id: "other",
  });
  expect(s.messages[0]).toMatchObject({ status: "pending" });
  expect(s.notices.map((n) => n.text)).toEqual(["Conversation not found."]);
});

test("an error with no conversation_id at all leaves the pending message alone", () => {
  const s = applyEvent(base(), {
    type: "error", code: "bad_request", message: "Invalid event.", message_id: null, conversation_id: null,
  });
  expect(s.messages[0]).toMatchObject({ status: "pending" });
  expect(s.notices.map((n) => n.text)).toEqual(["Invalid event."]);
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

test("task_created confirms the pending bubble and task_state appends the reply", () => {
  let s = applyEvent(base(), { type: "task_created", task_id: "t1", conversation_id: "c1", goal: "hi",
    user_message_id: "u1", client_id: "k1" });
  expect(s.messages[0]).toMatchObject({ id: "u1", status: "complete" });
  s = applyEvent(s, { type: "task_state", task_id: "t1", conversation_id: "c1", state: "running", summary: null,
    error: null, message_id: null, message_text: null });
  expect(s.messages).toHaveLength(1);
  s = applyEvent(s, { type: "task_state", task_id: "t1", conversation_id: "c1", state: "done", summary: "ok",
    error: null, message_id: "m2", message_text: "All done." });
  s = applyEvent(s, { type: "task_state", task_id: "t1", conversation_id: "c1", state: "done", summary: "ok",
    error: null, message_id: "m2", message_text: "All done." });
  expect(s.messages.map((m) => [m.id, m.content])).toEqual([["u1", "hi"], ["m2", "All done."]]);
});

test("facts_changed notes the user message and context_used records what a reply recalled", () => {
  let s = applyEvent(base(), {
    type: "message_start", conversation_id: "c1", message_id: "a1", user_message_id: "u1", client_id: "k1", role: "assistant",
  });
  s = applyEvent(s, {
    type: "context_used", message_id: "a1", facts: [{ id: "f1", text: "Has a dog called Pip" }], episodes: [],
  });
  s = applyEvent(s, {
    type: "facts_changed", conversation_id: "c1", message_id: "u1",
    changes: [{ fact_id: "f2", op: "add", scope: "user", text: "Lives in Leeds", old_text: null, undone: false }],
  });
  expect(s.messages[0].noted?.[0].text).toBe("Lives in Leeds");
  expect(s.messages[1].recalled).toEqual({ facts: [{ id: "f1", text: "Has a dog called Pip" }], episodes: [] });
  expect(applyEvent(s, { type: "context_used", message_id: "nope", facts: [], episodes: [] })).toBe(s);
});

const started = () => applyEvent(base(), {
  type: "message_start", conversation_id: "c1", message_id: "a1", user_message_id: "u1", client_id: "k1", role: "assistant",
});
const rain = [{ n: 1, title: "Rain in Paris", url: "https://a.example/rain" }];

test("web activity shows while a tool runs and clears when text arrives or the message ends", () => {
  let s = applyEvent(started(), { type: "tool_activity", message_id: "a1", task_id: null, kind: "search", label: 'Searching "rain"' });
  expect(s.messages[1].activity).toBe('Searching "rain"');
  s = applyEvent(s, { type: "tool_activity", message_id: "a1", task_id: null, kind: "read", label: "Reading a.example" });
  expect(s.messages[1].activity).toBe("Reading a.example");     // the latest one
  s = applyEvent(s, { type: "token", message_id: "a1", text: "It" });
  expect(s.messages[1]).toMatchObject({ content: "It", activity: undefined });
  s = applyEvent(s, { type: "tool_activity", message_id: "a1", task_id: null, kind: "search", label: "Searching again" });
  s = applyEvent(s, { type: "message_end", message_id: "a1", status: "stopped" });
  expect(s.messages[1].activity).toBeUndefined();
  // activity of a message that isn't here, or of a task (no message), changes nothing
  expect(applyEvent(s, { type: "tool_activity", message_id: "nope", task_id: null, kind: "read", label: "x" })).toBe(s);
  expect(applyEvent(s, { type: "tool_activity", message_id: null, task_id: "t1", kind: "read", label: "x" })).toBe(s);
});

test("sources attach to the reply; a task's wait for the message its answer arrives in", () => {
  let s = applyEvent(started(), { type: "sources", message_id: "a1", task_id: null, sources: rain });
  expect(s.messages[1].sources).toEqual(rain);
  // a task: Sources comes before the task_state that creates its final message
  s = applyEvent(base(), { type: "sources", message_id: "m9", task_id: "t1", sources: rain });
  expect(s.messages).toHaveLength(1);
  expect(s.pendingSources).toEqual({ m9: rain });
  s = applyEvent(s, { type: "task_state", task_id: "t1", conversation_id: "c1", state: "done", summary: "Done.", error: null,
    message_id: "m9", message_text: "It rains [1]." });
  expect(s.messages[1]).toMatchObject({ id: "m9", content: "It rains [1].", sources: rain });
  expect(s.pendingSources).toEqual({});
});

test("toUiMessages restores a reply's sources", () => {
  const [a] = toUiMessages([{ id: "a1", conversation_id: "c1", role: "assistant", content: "x [1]", status: "complete",
    created_at: "", meta: { sources: rain } }]);
  expect(a.sources).toEqual(rain);
  expect(toUiMessages([{ id: "b", conversation_id: "c", role: "assistant", content: "", status: "complete", created_at: "", meta: {} }])[0].sources).toBeUndefined();
});

test("toUiMessages maps remembered and recalled meta", () => {
  const [u, a] = toUiMessages([
    { id: "u1", conversation_id: "c1", role: "user", content: "I live in Leeds", status: "complete", created_at: "",
      meta: { facts_changed: [{ fact_id: "f", op: "add", scope: "user", text: "Lives in Leeds", old_text: null, undone: true }] } },
    { id: "a1", conversation_id: "c1", role: "assistant", content: "Nice", status: "complete", created_at: "",
      meta: { context: { facts: [], episodes: [{ id: "3", conversation_id: "c0", text: "User: hi", created_at: "" }] } } },
  ]);
  expect(u.noted?.[0].undone).toBe(true);
  expect(a.recalled?.episodes[0].conversation_id).toBe("c0");
  expect(toUiMessages([{ id: "x", conversation_id: "c", role: "assistant", content: "", status: "complete", created_at: "", meta: {} }])[0].recalled).toBeUndefined();
});
