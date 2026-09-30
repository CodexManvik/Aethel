"""Token usage over time, for Settings → Usage (token-efficiency spec §3)."""
from fastapi import APIRouter, Depends, Query

from ...services import Services
from ..deps import get_services, require_auth

router = APIRouter(prefix="/api/usage", dependencies=[Depends(require_auth)])


@router.get("")
def usage(days: int = Query(default=7, ge=1, le=90), svc: Services = Depends(get_services)) -> dict:
    return svc.usage.totals(days)
