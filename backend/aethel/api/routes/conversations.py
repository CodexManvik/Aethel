import shutil

import anyio
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from ...paths import aethel_home
from ...services import Services
from ...store.repos import Conversation, Message
from ..deps import get_services, require_auth

router = APIRouter(prefix="/api/conversations", dependencies=[Depends(require_auth)])


class CreateIn(BaseModel):
    persona_id: str = "aethel"


class UpdateIn(BaseModel):
    """Either field may be sent. web: true or false, or null to follow Settings (absent leaves it alone)."""
    title: str | None = Field(default=None, max_length=200)
    web: bool | None = None


def _require(svc: Services, conv_id: str) -> Conversation:
    conv = svc.conversations.get(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.get("")
def list_conversations(svc: Services = Depends(get_services)) -> list[Conversation]:
    return svc.conversations.list()


@router.post("")
def create_conversation(body: CreateIn, svc: Services = Depends(get_services)) -> Conversation:
    """A conversation is with one persona for its whole life: talking to another starts a new conversation."""
    if svc.personas.get(body.persona_id) is None:
        raise HTTPException(status_code=422, detail="There's no persona with that id.")
    return svc.conversations.create(persona_id=body.persona_id)


@router.patch("/{conv_id}")
def update_conversation(conv_id: str, body: UpdateIn, svc: Services = Depends(get_services)) -> Conversation:
    conv = _require(svc, conv_id)
    if "title" in body.model_fields_set and body.title is not None:
        conv = svc.conversations.rename(conv_id, body.title.strip())
    if "web" in body.model_fields_set:  # null is a value here: "follow Settings"
        conv = svc.conversations.set_web(conv_id, body.web)
    return conv


@router.delete("/{conv_id}", status_code=204)
async def delete_conversation(conv_id: str, svc: Services = Depends(get_services)) -> Response:
    """Gone means gone: a task still running in it stops, and what it left behind goes too (the rows of its messages
    and tasks follow by cascade; these aren't rows of theirs)."""
    _require(svc, conv_id)
    task_ids = await svc.engine.stop_for_conversation(conv_id)

    def clear() -> None:
        svc.episodic.remove_conversation(conv_id)  # its earlier exchanges must never be recalled again
        for task_id in task_ids:
            svc.changes.forget_task(task_id)  # the copies of files it changed
            shutil.rmtree(aethel_home() / "media" / "tasks" / task_id, ignore_errors=True)  # its screenshots
    await anyio.to_thread.run_sync(clear)
    if not svc.conversations.delete(conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=204)


@router.get("/{conv_id}/messages")
def list_messages(conv_id: str, svc: Services = Depends(get_services)) -> list[Message]:
    _require(svc, conv_id)
    out = []
    for m in svc.messages.list(conv_id):
        if m.status == "streaming":
            live = svc.chat.partial(m.id)
            if live is not None:
                m = m.model_copy(update={"content": live})
        out.append(m)
    return out
