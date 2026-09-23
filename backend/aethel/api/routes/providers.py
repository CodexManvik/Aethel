import time

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...providers.base import ChatMessage, ProviderError, TextDelta
from ...providers.catalog import PROVIDERS, base_url_for
from ...providers.router import LOCAL_API_KEY
from ...services import Services
from ...settings import ProviderId, RouteEntry
from ..deps import get_services, require_auth

router = APIRouter(prefix="/api/providers", dependencies=[Depends(require_auth)])
PRIVATE_MODE_DETAIL = "Private mode is on."


def _refuse_cloud_in_private_mode(svc: Services, provider_id: str) -> None:
    """Private mode keeps everything on this machine: no cloud call, not even
    a model listing or a connection test."""
    if provider_id != "local" and svc.settings.get().private_mode:
        raise HTTPException(status_code=409, detail=PRIVATE_MODE_DETAIL)


class ProviderInfo(BaseModel):
    id: str
    label: str
    needs_key: bool
    has_key: bool
    base_url: str


class TestIn(BaseModel):
    provider: ProviderId
    model: str


class TestOut(BaseModel):
    ok: bool
    latency_ms: int
    reply: str | None = None
    error: str | None = None


@router.get("")
def list_providers(svc: Services = Depends(get_services)) -> list[ProviderInfo]:
    s = svc.settings.get()
    return [
        ProviderInfo(id=m.id, label=m.label, needs_key=m.needs_key,
                     has_key=(not m.needs_key) or svc.keys.get(m.id) is not None,
                     base_url=base_url_for(m.id, s))
        for m in PROVIDERS.values()
    ]


@router.get("/{provider_id}/models")
async def list_models(provider_id: str, svc: Services = Depends(get_services)) -> dict:
    if provider_id not in PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    _refuse_cloud_in_private_mode(svc, provider_id)
    if provider_id == "local":
        if svc.local_llm is None or not await anyio.to_thread.run_sync(svc.local_llm.is_up):
            return {"models": []}
        api_key = LOCAL_API_KEY
    else:
        api_key = svc.keys.get(provider_id)
        if not api_key:
            raise HTTPException(status_code=400, detail="No API key saved for this provider.")
    lister = svc.provider_factory(RouteEntry(provider=provider_id, model="_list"), api_key, svc.settings.get())
    try:
        return {"models": await lister.list_models()}
    except (ProviderError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/test")
async def test_provider(body: TestIn, svc: Services = Depends(get_services)) -> TestOut:
    _refuse_cloud_in_private_mode(svc, body.provider)
    if body.provider == "local":
        try:
            await anyio.to_thread.run_sync(svc.local_llm.ensure_running)
        except Exception as exc:
            return TestOut(ok=False, latency_ms=0, error=str(exc))
        api_key = LOCAL_API_KEY
    else:
        api_key = svc.keys.get(body.provider)
        if not api_key:
            return TestOut(ok=False, latency_ms=0, error="No API key saved for this provider.")
    provider = svc.provider_factory(RouteEntry(provider=body.provider, model=body.model), api_key,
                                    svc.settings.get())
    started = time.perf_counter()
    parts: list[str] = []
    try:
        async for ev in provider.stream([ChatMessage("user", "Reply with the single word: ok")],
                                        temperature=0.0, max_tokens=16):
            if isinstance(ev, TextDelta):
                parts.append(ev.text)
    except ProviderError as exc:
        return TestOut(ok=False, latency_ms=int((time.perf_counter() - started) * 1000), error=str(exc))
    return TestOut(ok=True, latency_ms=int((time.perf_counter() - started) * 1000), reply="".join(parts).strip())
