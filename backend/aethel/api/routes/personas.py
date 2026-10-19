"""The persona studio (spec §9, §12.3): personas, drafting one from a description, their files, style import."""
import re
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ...personas.accent import accent_from_rgb
from ...personas.distill import ExportError, directive, extract_style_metrics, of_speaker, parse_export, speakers
from ...personas.draft import DESCRIPTION_MAX, DraftError, draft_persona
from ...personas.model import MAX_SECTIONS, SECTIONS, Persona, PersonaVoice
from ...protocol import PersonaUpdated
from ...providers.base import ProviderError
from ...providers.router import NoProviderAvailable
from ...services import Services
from ..deps import get_services, require_auth

router = APIRouter(prefix="/api/personas", dependencies=[Depends(require_auth)])
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
EXPORT_MAX = 20_000_000
DIRECTIVE_MAX = 2000
HEX = r"^#[0-9a-fA-F]{6}$"
SECTION_MAX = 6000  # each section leads every reply's prompt: a cap keeps one from eating the budget
Sections = Annotated[dict[str, Annotated[str, Field(max_length=SECTION_MAX)]], Field(max_length=MAX_SECTIONS)]
_pending: dict[str, tuple[str, list]] = {}  # persona id -> (import id, the chosen speaker's lines), until saved


class PersonaOut(Persona):
    style: str | None = None  # the imported style directive (style.md)
    lines: int = 0            # imported messages kept as style examples


class PersonaIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    sections: Sections = {}
    voice: PersonaVoice = Field(default_factory=PersonaVoice)
    image_style: str = Field(default="", max_length=200)
    image_frequency: Literal["never", "on_request", "sometimes"] = "on_request"
    tone_engine: bool = True


class PersonaPatch(BaseModel):
    """Only the fields sent change; sections are merged (send one to change one). accent_pick: a colour chosen by
    hand (the light and dark versions are derived from it), or null to go back to the portrait's."""
    name: str | None = Field(default=None, min_length=1, max_length=60)
    sections: Sections | None = None
    voice: PersonaVoice | None = None
    image_style: str | None = Field(default=None, max_length=200)
    image_frequency: Literal["never", "on_request", "sometimes"] | None = None
    tone_engine: bool | None = None
    accent_pick: str | None = Field(default=None, pattern=HEX)


class DraftIn(BaseModel):
    description: str = Field(min_length=3, max_length=DESCRIPTION_MAX)


class ImportIn(BaseModel):
    filename: str = Field(default="", max_length=200)
    content: str = Field(min_length=1, max_length=EXPORT_MAX)
    speaker: str | None = Field(default=None, max_length=60)


class SpeakerOut(BaseModel):
    name: str
    messages: int


class ImportOut(BaseModel):
    speakers: list[SpeakerOut]
    speaker: str | None = None
    messages: int = 0
    directive: str | None = None
    import_id: str | None = None  # send it back with the saved directive to keep the lines as style examples


class StyleIn(BaseModel):
    directive: str = Field(max_length=DIRECTIVE_MAX)
    import_id: str | None = None


def _out(svc: Services, p: Persona) -> PersonaOut:
    return PersonaOut(**p.model_dump(), style=svc.personas.style_directive(p.id), lines=svc.persona_lines.count(p.id))


def _require(svc: Services, pid: str) -> Persona:
    p = svc.personas.get(pid)
    if p is None:
        raise HTTPException(status_code=404, detail="There's no persona with that id.")
    return p


async def _changed(svc: Services, pid: str, deleted: bool = False) -> None:
    await svc.hub.publish(PersonaUpdated(persona_id=pid, deleted=deleted))


@router.get("")
def list_personas(svc: Services = Depends(get_services)) -> list[PersonaOut]:
    return [_out(svc, p) for p in svc.personas.list()]


@router.post("/draft")
async def draft(body: DraftIn, svc: Services = Depends(get_services)) -> Persona:
    """A first draft to edit; nothing is saved."""
    try:
        return await draft_persona(svc.router, body.description)
    except NoProviderAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (DraftError, ProviderError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/{pid}")
def get_persona(pid: str, svc: Services = Depends(get_services)) -> PersonaOut:
    return _out(svc, _require(svc, pid))


@router.post("")
async def create_persona(body: PersonaIn, svc: Services = Depends(get_services)) -> PersonaOut:
    sections = {**{s: "" for s in SECTIONS}, **body.sections}
    try:
        made = svc.personas.create(Persona(id="new", name=body.name.strip(), voice=body.voice, sections=sections,
                                           image_style=body.image_style, image_frequency=body.image_frequency,
                                           tone_engine=body.tone_engine))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _changed(svc, made.id)
    return _out(svc, made)


@router.patch("/{pid}")
async def update_persona(pid: str, body: PersonaPatch, svc: Services = Depends(get_services)) -> PersonaOut:
    _require(svc, pid)
    patch: dict[str, Any] = body.model_dump(exclude_unset=True, exclude={"accent_pick"})
    patch = {k: v for k, v in patch.items() if v is not None}
    if "name" in patch:
        patch["name"] = patch["name"].strip()
    if "accent_pick" in body.model_fields_set:
        pick = body.accent_pick
        patch["accent"] = (accent_from_rgb(tuple(int(pick[i:i + 2], 16) for i in (1, 3, 5)), "manual").model_dump()
                           if pick else None)
    try:
        updated = svc.personas.update(pid, patch)
    except KeyError as exc:  # deleted meanwhile
        raise HTTPException(status_code=404, detail="There's no persona with that id.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _changed(svc, pid)
    return _out(svc, updated)


@router.delete("/{pid}", status_code=204)
async def delete_persona(pid: str, svc: Services = Depends(get_services)) -> Response:
    p = _require(svc, pid)
    if p.built_in:
        raise HTTPException(status_code=409, detail="The built-in persona can't be deleted.")
    svc.persona_lines.remove(pid)
    svc.personas.delete(pid)
    _pending.pop(pid, None)
    await _changed(svc, pid, deleted=True)
    return Response(status_code=204)


@router.get("/{pid}/files/{path:path}")
def persona_file(pid: str, path: str, svc: Services = Depends(get_services)) -> FileResponse:
    """A picture in the persona's folder (portraits, the gallery); nothing outside it, and nothing but pictures."""
    _require(svc, pid)
    folder = svc.personas.folder(pid).resolve()
    target = (folder / path).resolve()
    if (not target.is_relative_to(folder) or not target.is_file() or target.suffix.lower() not in IMAGE_TYPES
            or any(part.startswith(".") for part in Path(path).parts)):
        raise HTTPException(status_code=404, detail="No such file.")
    return FileResponse(target, media_type=IMAGE_TYPES[target.suffix.lower()])


@router.post("/{pid}/style/import")
async def import_style(pid: str, body: ImportIn, svc: Services = Depends(get_services)) -> ImportOut:
    """First call: who speaks in the export. Second call, with a speaker: their style as a directive to edit."""
    p = _require(svc, pid)
    try:
        lines = await anyio.to_thread.run_sync(parse_export, body.content, body.filename)
    except ExportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    who = [SpeakerOut(name=n, messages=c) for n, c in speakers(lines)]
    if body.speaker is None:
        return ImportOut(speakers=who)
    mine = of_speaker(lines, body.speaker)
    if not mine:
        raise HTTPException(status_code=422, detail="That speaker has no messages in this file.")
    metrics = extract_style_metrics(lines, body.speaker)
    import_id = uuid.uuid4().hex
    _pending[pid] = (import_id, mine)
    return ImportOut(speakers=who, speaker=body.speaker, messages=len(mine), directive=directive(metrics, p.name),
                     import_id=import_id)


@router.put("/{pid}/style")
async def save_style(pid: str, body: StyleIn, svc: Services = Depends(get_services)) -> PersonaOut:
    """The directive (empty clears it); with the import's id, its lines become the persona's style examples."""
    p = _require(svc, pid)
    pending = _pending.get(pid)
    if body.import_id is not None and (pending is None or pending[0] != body.import_id):
        raise HTTPException(status_code=409, detail="That import has expired; import the file again.")
    svc.personas.set_style_directive(pid, re.sub(r"\n{3,}", "\n\n", body.directive))
    if body.import_id is not None:
        _pending.pop(pid, None)
        await anyio.to_thread.run_sync(svc.persona_lines.replace, pid, pending[1])
        if svc.personas.get(pid) is None:  # deleted while the lines were being embedded
            svc.persona_lines.remove(pid)
            raise HTTPException(status_code=404, detail="There's no persona with that id.")
    await _changed(svc, pid)
    return _out(svc, p)


@router.delete("/{pid}/style", status_code=204)
async def clear_style(pid: str, svc: Services = Depends(get_services)) -> Response:
    _require(svc, pid)
    svc.personas.set_style_directive(pid, "")
    svc.persona_lines.remove(pid)
    await _changed(svc, pid)
    return Response(status_code=204)
