import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ..auth import origin_allowed
from ..protocol import ApprovalDecision, ErrorEvent, KillSwitch, StartTask, StopGeneration, TaskControl, UserMessage, \
    client_event_adapter

router = APIRouter()


@router.websocket("/ws/session")
async def session_socket(websocket: WebSocket) -> None:
    services = websocket.app.state.services
    # Browsers always send Origin on a WS handshake: refuse foreign pages
    # (cross-site WebSocket hijacking). CORS doesn't cover WebSockets.
    if not origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=4403)
        return
    if not services.auth.check(websocket.query_params.get("token")):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    send_lock = asyncio.Lock()

    async def send(payload: str) -> None:
        async with send_lock:
            await websocket.send_text(payload)

    closing: set[asyncio.Task] = set()

    def dropped() -> None:
        # The hub gave up on this socket: close it so the window reconnects
        # and re-fetches, rather than silently missing every later event.
        task = asyncio.get_running_loop().create_task(websocket.close(code=1011))
        closing.add(task)
        task.add_done_callback(closing.discard)

    unsubscribe = services.hub.subscribe(send, on_drop=dropped)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                event = client_event_adapter.validate_json(raw)
            except ValidationError:
                await send(ErrorEvent(message="Invalid event.", code="bad_request").model_dump_json())
                continue
            if isinstance(event, UserMessage):
                services.chat.start_turn(event)
            elif isinstance(event, StopGeneration):
                await services.chat.stop(event.message_id)
            elif isinstance(event, StartTask):
                await services.engine.start(conversation_id=event.conversation_id, goal=event.goal,
                                            client_id=event.client_id)
            elif isinstance(event, TaskControl):
                action = {"pause": services.engine.pause, "resume": services.engine.resume,
                          "cancel": services.engine.cancel}[event.action]
                await action(event.task_id)
            elif isinstance(event, ApprovalDecision):
                await services.approvals.resolve(event.approval_id, event.decision)
            elif isinstance(event, KillSwitch):
                await services.engine.cancel_all()
    except WebSocketDisconnect:
        pass
    finally:
        unsubscribe()
