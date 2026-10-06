from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

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
def delete_conversation(conv_id: str, svc: Services = Depends(get_services)) -> Response:
    svc.episodic.remove_conversation(conv_id)  # its earlier exchanges must never be recalled again
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
