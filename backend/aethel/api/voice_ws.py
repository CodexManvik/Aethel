import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth import origin_allowed
from ..voice.session import VoiceSession

router = APIRouter()
log = logging.getLogger("aethel.voice")


@router.websocket("/ws/voice")
async def voice_socket(websocket: WebSocket) -> None:
    services = websocket.app.state.services
    # The same checks as /ws/session: a page on another origin must not be able to open the microphone's twin.
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

    async def send_bytes(payload: bytes) -> None:
        async with send_lock:
            await websocket.send_bytes(payload)

    session = VoiceSession(services, send, send_bytes)
    try:
        while not session.ended:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("text") is not None:
                await session.on_text(message["text"])
            elif message.get("bytes") is not None:
                await session.on_audio(message["bytes"])
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("the voice socket failed")
    finally:
        await session.close()
        try:
            await websocket.close()
        except Exception:
            pass
