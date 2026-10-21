"""In-app downloads (spec 2026-10-19 §I3): voices, a llama.cpp server, local image support."""
from fastapi import APIRouter, Depends, HTTPException

from ...images.models import ModelError
from ...services import Services
from ..deps import get_services, require_auth

router = APIRouter(prefix="/api/downloads", dependencies=[Depends(require_auth)])


@router.get("")
def downloads_status(svc: Services = Depends(get_services)) -> dict:
    return svc.downloads.status()


@router.post("/{name}", status_code=202)
def start_download(name: str, svc: Services = Depends(get_services)) -> dict:
    if name not in svc.downloads.jobs:
        raise HTTPException(status_code=404, detail="Nothing to download by that name.")
    if svc.settings.get().private_mode:
        raise HTTPException(status_code=409, detail="Private mode is on: nothing is downloaded.")
    try:
        return {"started": svc.downloads.start(name)}
    except ModelError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
