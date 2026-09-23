import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from ..auth import origin_allowed
from .events import ErrorEvent, StopGeneration, UserMessage, client_event_adapter

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
    state = {"open": True}

    async def emit(event: BaseModel) -> None:
        # Generation keeps going (and persists) even if the socket went away.
        if not state["open"]:
            return
        async with send_lock:
            try:
                await websocket.send_text(event.model_dump_json())
            except Exception:
                state["open"] = False

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                event = client_event_adapter.validate_json(raw)
            except ValidationError:
                await emit(ErrorEvent(message="Invalid event.", code="bad_request"))
                continue
            if isinstance(event, UserMessage):
                services.chat.start_turn(event, emit)
            elif isinstance(event, StopGeneration):
                await services.chat.stop(event.message_id, emit)
    except WebSocketDisconnect:
        state["open"] = False
