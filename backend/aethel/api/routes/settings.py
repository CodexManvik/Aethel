import os

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError

from ...services import Services
from ...settings import AppSettings
from ..deps import get_services, require_auth

router = APIRouter(prefix="/api/settings", dependencies=[Depends(require_auth)])


@router.get("")
def get_settings(svc: Services = Depends(get_services)) -> AppSettings:
    return svc.settings.get()


@router.patch("")
async def patch_settings(patch: dict = Body(...), svc: Services = Depends(get_services)) -> AppSettings:
    try:
        updated = svc.settings.update(patch)
    except ValidationError as exc:
        # without the context: it can hold the ValueError itself, which isn't JSON (the message says it all)
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False, include_context=False)) from exc
    if "system1" in patch and os.environ.get("AETHEL_SYSTEM1") != "0":
        svc.system1.start()  # switched on just now: it wasn't loaded at startup, and a failed load gets another try
    return updated
