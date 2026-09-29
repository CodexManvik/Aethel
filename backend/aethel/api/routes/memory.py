"""Procedural memory for the Memory screen (spec §12.3): skills and app notes."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...services import Services
from ..deps import get_services, require_auth

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
