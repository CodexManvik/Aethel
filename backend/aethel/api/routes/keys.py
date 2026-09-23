from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...services import Services
from ..deps import get_services, require_auth

router = APIRouter(prefix="/api/keys", dependencies=[Depends(require_auth)])


class KeysIn(BaseModel):
    keys: dict[str, str | None]


@router.post("")
def set_keys(body: KeysIn, svc: Services = Depends(get_services)) -> dict[str, bool]:
    try:
        svc.keys.set_many(body.keys)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return svc.keys.status()


@router.get("/status")
def key_status(svc: Services = Depends(get_services)) -> dict[str, bool]:
    return svc.keys.status()
