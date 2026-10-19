"""Photos (spec §10): what can make them, the face models, retry, the gallery, and the studio's faces."""
import asyncio
import os
import shutil
import threading

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException
from PIL import Image
from pydantic import BaseModel

from ...images.models import FACE_MODELS, ModelError, download, is_installed
from ...images.service import GALLERY
from ...personas.accent import accent_for
from ...protocol import PersonaUpdated
from ...services import Services
from ..deps import get_services, require_auth

router = APIRouter(dependencies=[Depends(require_auth)])
PORTRAIT_SCENE = ("a natural head-and-shoulders portrait photo, looking at the camera, relaxed expression, plain softly "
                  "lit background")
SHEET = [  # file, scene, framing, expression: the character sheet drawn from the anchor
    ("sheet-34.jpg", "a three-quarter view portrait, head turned slightly to the side, plain background", "portrait", "neutral"),
    ("sheet-profile.jpg", "a side profile portrait, plain background", "portrait", "neutral"),
    ("sheet-smile.jpg", "a portrait with a warm, open smile, plain background", "portrait", "smiling"),
    ("sheet-laugh.jpg", "a candid portrait mid-laugh, plain background", "portrait", "laughing"),
]
FACES = 4


class Download:
    """The face models' download, one at a time, in a thread (about 250 MB)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.done = 0
        self.total = sum(m["size"] for m in FACE_MODELS.values())
        self.error: str | None = None

    def run(self) -> None:
        try:
            base = 0
            with httpx.Client(trust_env=False) as client:
                for name, spec in FACE_MODELS.items():
                    if not is_installed(name):
                        download(name, client, progress=lambda d, _t, b=base: setattr(self, "done", b + d))
                    base += spec["size"]
                    self.done = base
        except (ModelError, httpx.HTTPError, OSError) as exc:
            self.error = str(exc)
        except Exception as exc:  # whatever it was, the studio must hear the download stopped
            self.error = f"The download failed ({type(exc).__name__})."
        finally:
            self.running = False


_download = Download()


class ImagesStatus(BaseModel):
    backend: str | None      # what would draw the next photo, e.g. "gemini:gemini-3.1-flash-image"
    why_not: str             # when there's none: what to do about it
    face_models: bool        # the identity check is installed
    downloading: bool
    progress: float          # 0–1 of the face models' download
    error: str | None


class ReferenceIn(BaseModel):
    on: bool


class AnchorIn(BaseModel):
    image_id: str


def _persona(svc: Services, pid: str):
    p = svc.personas.get(pid)
    if p is None:
        raise HTTPException(status_code=404, detail="There's no persona with that id.")
    return p


@router.get("/api/images/status")
async def images_status(svc: Services = Depends(get_services)) -> ImagesStatus:
    backend = await svc.images.backend()
    why = ""
    if backend is None:
        s = svc.settings.get()
        reasons = [(await b.available())[1] for n, b in svc.images.backends.items()
                   if n in s.images.backends and not (s.private_mode and b.cloud)]
        why = ("In private mode only a model on this PC can make photos, and there's none in models/image/."
               if s.private_mode else next((r for r in reasons if r), "No image model is set up."))
    installed = all(is_installed(n) for n in FACE_MODELS)
    return ImagesStatus(backend=backend.name if backend else None, why_not=why, face_models=installed,
                        downloading=_download.running, progress=min(1.0, _download.done / _download.total),
                        error=_download.error)


@router.post("/api/images/face-models", status_code=202)
def download_face_models() -> dict:
    """Starts the download (the user asked for it in the studio); poll /api/images/status."""
    with _download.lock:
        if not _download.running and not all(is_installed(n) for n in FACE_MODELS):
            _download.running, _download.error, _download.done = True, None, 0
            threading.Thread(target=_download.run, name="face-models", daemon=True).start()
    return {"started": _download.running}


@router.get("/api/images/gemini-models")
async def gemini_models(svc: Services = Depends(get_services)) -> list[str]:
    try:
        return await svc.images.backends["gemini"].models()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't list Gemini's models: {exc}") from exc


@router.post("/api/images/{image_id}/retry")
async def retry(image_id: str, svc: Services = Depends(get_services)) -> dict:
    try:
        return {"image_id": await svc.images.retry(image_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="That photo can't be retried any more.") from exc


@router.get("/api/personas/{pid}/gallery")
def gallery(pid: str, svc: Services = Depends(get_services)) -> list[dict]:
    _persona(svc, pid)
    return svc.images.gallery(pid)


@router.post("/api/personas/{pid}/gallery/{image_id}/reference")
def set_reference(pid: str, image_id: str, body: ReferenceIn, svc: Services = Depends(get_services)) -> dict:
    _persona(svc, pid)
    try:
        return svc.images.set_reference(pid, image_id, body.on)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="No such photo.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/personas/{pid}/portraits")
async def portraits(pid: str, svc: Services = Depends(get_services)) -> list[dict]:
    """Four faces from the persona's image style alone, to pick the anchor from."""
    p = _persona(svc, pid)
    if await svc.images.backend() is None:
        raise HTTPException(status_code=503, detail="No image model is available.")
    return list(await asyncio.gather(*[svc.images.generate(persona=p, scene=PORTRAIT_SCENE, reason="studio",
                                                           references=False) for _ in range(FACES)]))


@router.post("/api/personas/{pid}/anchor")
async def set_anchor(pid: str, body: AnchorIn, svc: Services = Depends(get_services)) -> dict:
    """A gallery photo becomes the face: portraits/anchor.jpg, the accent colour (unless picked by hand), the
    anchor embedding."""
    p = _persona(svc, pid)
    folder = svc.personas.folder(pid)
    source = folder / GALLERY / f"{body.image_id}.jpg"
    if not body.image_id.isalnum() or not source.is_file():
        raise HTTPException(status_code=404, detail="No such photo.")
    (folder / "portraits").mkdir(exist_ok=True)

    def copy() -> None:  # aside, then moved into place: a photo being made may be reading the old anchor
        tmp = folder / "portraits" / ".anchor.jpg.tmp"
        shutil.copyfile(source, tmp)
        os.replace(tmp, folder / "portraits" / "anchor.jpg")
    await anyio.to_thread.run_sync(copy)
    patch: dict = {"anchors": ["portraits/anchor.jpg"], "sheet": []}  # the old sheet shows the old face
    if p.accent is None or p.accent.source == "anchor":
        accent = await anyio.to_thread.run_sync(lambda: accent_for(Image.open(source)))
        patch["accent"] = accent.model_dump() if accent else None
    updated = svc.personas.update(pid, patch)
    await anyio.to_thread.run_sync(svc.images.anchor_embedding, updated)
    await svc.hub.publish(PersonaUpdated(persona_id=pid))
    return updated.model_dump()


@router.post("/api/personas/{pid}/sheet")
async def make_sheet(pid: str, svc: Services = Depends(get_services)) -> dict:
    """The character sheet: a ¾ view, a profile and two expressions, drawn with the anchor as reference, each
    identity-checked (one more try below τ_id)."""
    p = _persona(svc, pid)
    if not p.anchors:
        raise HTTPException(status_code=409, detail="Pick a face first.")
    if await svc.images.backend() is None:
        raise HTTPException(status_code=503, detail="No image model is available.")
    p = svc.personas.update(pid, {"sheet": []})  # drawn from the anchor alone, not from the old sheet
    folder = svc.personas.folder(pid)
    sheet, results = [], []
    for file, scene, framing, expression in SHEET:
        got = await svc.images.generate(persona=p, scene=scene, framing=framing, expression=expression, reason="studio")
        results.append(got)
        if got["ok"]:
            shutil.copyfile(folder / GALLERY / f"{got['id']}.jpg", folder / "portraits" / file)
            sheet.append(f"portraits/{file}")
    updated = svc.personas.update(pid, {"sheet": sheet})
    await svc.hub.publish(PersonaUpdated(persona_id=pid))
    return {"persona": updated.model_dump(), "results": results}
