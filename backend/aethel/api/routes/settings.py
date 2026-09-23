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
def patch_settings(patch: dict = Body(...), svc: Services = Depends(get_services)) -> AppSettings:
    try:
        return svc.settings.update(patch)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc
