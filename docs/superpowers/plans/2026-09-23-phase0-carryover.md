# Phase 0: items carried over to Phase 1

Phase 0 (Foundation) finished on `revamp` at commit a5fd333. It passed its per-task reviews, a whole-branch review, and a fix wave. The items below were **deliberately deferred**, and the Phase 1 plan must include them.

## Do these first in Phase 1 (the task engine builds on them)
1. **Session hub.** Emit events to every live socket, not only the one that started the turn. Overlay the in-flight partial content in `GET /api/conversations/{id}/messages`, or persist every N tokens. Refetch the open conversation when the socket reopens and on `message_end`. Today, navigating away and back mid-reply, or a reconnect mid-reply, shows a stale or partial reply.
2. **Streaming hygiene in `ChatService._turn`.** Wrap `router.stream` in `contextlib.aclosing`. Swallow only cancellations that come from `stop()`, and re-raise the rest.
3. **Protocol layering.** Move the event models out of `aethel/api/events.py` into `aethel/protocol.py`, so the service layer stops importing the API layer.
4. **Scope `ProviderSwitched`.** Give it a `message_id` (and later a `task_id`), and make the frontend reducer attribute errors and notices to the right conversation.

## Smaller follow-ups
- WS `close(4401/4403)` before `accept` shows up as HTTP 403, which the client sees as 1006. Accept, then close. Stop retrying on 4401 in `ws.ts`. Have Boot make one authenticated call.
- The CSP hard-codes port 8765, but `AETHEL_PORT` can change it.
- There's no staleness check for `events.gen.ts` against `events.schema.json` in CI. The REST TS types are mirrored by hand; consider generating them from OpenAPI.
- `secret_get_all` fails entirely on a single bad credential. Skip bad entries, and make `pushStoredKeys` non-fatal.
- Settings and send mutations have no error toasts. An `ErrorEvent` without a `message_id` leaves a pending user message stuck.
- Deleting a conversation doesn't cancel its in-flight turn.
- A `local_llm` settings change doesn't restart a running llama-server.
- Mid-stream httpx read errors surface as `internal`, not `provider_error`.
- In private mode the vision role falls back to local even with no local VLM configured. The spec says it should be disabled.
- The auth-sweep test skips routes without `methods` (future `Mount`s). Fail on unknown route types.
- A job-object assign failure should log `GetLastError`. The log redaction misses trace-level ASGI scope dicts.
- There's no single-instance guard for the backend.
- PromptBox autosize should re-measure on resize (ResizeObserver or `field-sizing: content`).
- The no-provider error copy should lead with a friendly sentence, with the diagnostics secondary.
- Vite chunk-size warning (535 kB). Split vendor chunks.
- (Done) Spec §3 now names the route `POST /api/keys`.
