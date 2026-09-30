"""The Memory screen (spec §12.3): remembered facts, earlier conversations, skills and app notes."""
import asyncio
import logging
import threading
from typing import Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from ...memory.facts import MAX_FACT_CHARS, Fact, FactEvent
from ...protocol import FactChange
from ...services import Services
from ..deps import get_services, require_auth

log = logging.getLogger("aethel.api")

router = APIRouter(prefix="/api/memory", dependencies=[Depends(require_auth)])


class SkillOut(BaseModel):
    id: str
    title: str
    intent: str
    apps: list[str]
    status: Literal["quarantined", "approved", "deprecated"]
    runs: int
    successes: int
    avg_duration_s: float | None
    duration_history: list[float]
    last_used: str | None
    macro: str
    steps: list[str]
    pitfalls: list[str]


class NoteOut(BaseModel):
    app: str
    facts: list[str]
    body: str
    updated: str | None = None


class StatusIn(BaseModel):
    status: Literal["quarantined", "approved", "deprecated"]


class NoteIn(BaseModel):
    body: str = Field(max_length=20_000)


def _skill(d: dict) -> SkillOut:
    return SkillOut(**{k: d.get(k) for k in SkillOut.model_fields}
                    | {"last_used": str(d["last_used"]) if d.get("last_used") else None})


def _note(d: dict) -> NoteOut:
    return NoteOut(app=d["app"], facts=d["facts"], body=d["body"],
                   updated=str(d["updated"]) if d.get("updated") else None)


@router.get("/skills")
def list_skills(svc: Services = Depends(get_services)) -> list[SkillOut]:
    return [_skill(d) for d in svc.knowledge.skills()]


@router.patch("/skills/{skill_id}")
def set_skill_status(skill_id: str, body: StatusIn, svc: Services = Depends(get_services)) -> SkillOut:
    doc = svc.knowledge.set_status(skill_id, body.status)
    if doc is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return _skill(doc)


@router.get("/notes")
def list_notes(svc: Services = Depends(get_services)) -> list[NoteOut]:
    return [_note(d) for d in svc.knowledge.notes()]


@router.put("/notes/{app}")
def save_note(app: str, body: NoteIn, svc: Services = Depends(get_services)) -> NoteOut:
    return _note(svc.knowledge.save_note_body(app, body.body))


@router.get("/system1")
def system1_status(svc: Services = Depends(get_services)) -> dict:
    return {"status": svc.system1.status()}


# ---- facts (Phase 3 spec §5) --------------------------------------------------------------
class FactOut(Fact):
    conversation_title: str | None = None
    added_by: Literal["extractor", "user"] = "user"  # who first added it (its source may be gone since)


class FactIn(BaseModel):
    scope: str = Field(default="user", pattern=r"^(user|persona:[a-z0-9_-]{1,64})$")
    text: str = Field(min_length=1, max_length=MAX_FACT_CHARS)


class FactEdit(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_FACT_CHARS)


class UndoIn(BaseModel):
    message_id: str
    index: int = Field(ge=0)


def _fact_out(svc: Services, fact: Fact) -> FactOut:
    conv = svc.conversations.get(fact.conversation_id) if fact.conversation_id else None
    events = svc.facts.history(fact.id)
    return FactOut(**fact.model_dump(), conversation_title=conv.title if conv else None,
                   added_by=events[-1].actor if events else "user")


def _blank(text: str) -> str:
    text = text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="A fact can't be empty")
    return text


@router.get("/facts")
def list_facts(scope: str | None = None, q: str | None = None, svc: Services = Depends(get_services)) -> list[FactOut]:
    return [_fact_out(svc, f) for f in svc.facts.list(scope=scope, q=q)]


@router.post("/facts")
def add_fact(body: FactIn, svc: Services = Depends(get_services)) -> FactOut:
    return _fact_out(svc, svc.facts.add(body.scope, _blank(body.text), actor="user"))


@router.patch("/facts/{fact_id}")
def edit_fact(fact_id: str, body: FactEdit, svc: Services = Depends(get_services)) -> FactOut:
    fact = svc.facts.update(fact_id, _blank(body.text), actor="user")
    if fact is None:
        raise HTTPException(status_code=404, detail="Fact not found")
    return _fact_out(svc, fact)


@router.delete("/facts/{fact_id}", status_code=204)
def delete_fact(fact_id: str, svc: Services = Depends(get_services)) -> Response:
    if svc.facts.delete(fact_id, actor="user") is None:
        raise HTTPException(status_code=404, detail="Fact not found")
    return Response(status_code=204)


@router.get("/facts/{fact_id}/history")
def fact_history(fact_id: str, svc: Services = Depends(get_services)) -> list[FactEvent]:
    return svc.facts.history(fact_id)  # still answers after a delete: the log outlives the fact


_undo_lock = threading.Lock()  # a double click or a retry must not undo twice (e.g. re-add a fact twice)


@router.post("/facts/undo")
def undo_fact_change(body: UndoIn, svc: Services = Depends(get_services)) -> list[FactChange]:
    """Reverse one change the extractor made from a message (the 'Noted…' line's Undo)."""
    with _undo_lock:
        msg = svc.messages.get(body.message_id)
        raw = (msg.meta.get("facts_changed") if msg else None) or []
        if body.index >= len(raw):
            raise HTTPException(status_code=404, detail="No such change")
        changes = [FactChange.model_validate(c) for c in raw]
        c = changes[body.index]
        if c.undone:
            raise HTTPException(status_code=409, detail="Already undone")
        if c.op == "add":
            svc.facts.delete(c.fact_id, actor="user", source_message_id=msg.id)
        elif c.op == "update":
            current = svc.facts.get(c.fact_id)
            if current is not None and current.text != c.text:
                # reverting would throw away a newer edit
                raise HTTPException(status_code=409, detail="That fact has changed since; edit it in Memory")
            if current is not None and c.old_text:
                svc.facts.update(c.fact_id, c.old_text, actor="user", source_message_id=msg.id)
        elif c.op == "delete" and c.old_text:
            svc.facts.add(c.scope, c.old_text, actor="user", source_message_id=msg.id,
                          conversation_id=msg.conversation_id)
        changes[body.index] = c.model_copy(update={"undone": True})
        svc.messages.update(msg.id, meta={**msg.meta, "facts_changed": [x.model_dump() for x in changes]})
        return changes


# ---- episodic memory --------------------------------------------------------------------
@router.get("/episodic")
def episodic_status(svc: Services = Depends(get_services)) -> dict:
    return svc.episodic.status()


@router.post("/episodic/rebuild", status_code=202)
async def rebuild_episodic(svc: Services = Depends(get_services)) -> dict:
    if svc.episodic.rebuilding or _rebuilds:
        raise HTTPException(status_code=409, detail="Already rebuilding")

    async def run() -> None:
        try:
            await anyio.to_thread.run_sync(svc.episodic.rebuild)
        except Exception:
            log.exception("rebuilding the episodic index failed")

    task = asyncio.create_task(run())
    _rebuilds.add(task)
    task.add_done_callback(_rebuilds.discard)
    return {"started": True}


_rebuilds: set[asyncio.Task] = set()  # held so the rebuild isn't garbage-collected mid-run
