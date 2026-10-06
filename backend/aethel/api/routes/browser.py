"""Settings -> Browser: Aethel's own background browser (Phase 3 spec §7.4)."""
import asyncio
import shutil

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ...paths import aethel_home
from ...services import Services
from ..deps import get_services, require_auth

router = APIRouter(prefix="/api/browser", dependencies=[Depends(require_auth)])
CLEAR_ATTEMPTS, RETRY_PAUSE_S = 6, 0.4  # Edge may take a moment to let go of its files after it is stopped


class BrowserStatus(BaseModel):
    status: str          # the server's state (running, starting, failed: …), or "absent" when there is no browser
    show: bool           # whether it runs with a window (takes effect when it next starts)
    profile_exists: bool  # whether it has signed-in sites, cookies, history to clear
    signing_in: bool     # the window the user was given to sign in with is still open


def _in_use(svc: Services) -> None:
    """The browser can't be stopped, signed in on or cleared underneath a task that is using it."""
    if svc.engine.browser_in_use():
        raise HTTPException(status_code=409, detail="A task is using the browser right now. Wait for it to finish, "
                                                    "or stop the task, then try again.")


def _present(svc: Services) -> None:
    if "browser" not in svc.mcp.status():
        raise HTTPException(status_code=409, detail="There is no background browser on this computer (it needs "
                                                    "Node.js, which provides npx).")


@router.get("")
def browser_status(svc: Services = Depends(get_services)) -> BrowserStatus:
    profile = svc.browser.profile_dir
    return BrowserStatus(status=svc.mcp.status().get("browser", "absent"), show=svc.settings.get().browser.show,
                         profile_exists=profile.is_dir() and any(profile.iterdir()),
                         signing_in=svc.browser.signing_in())


@router.post("/sign-in", status_code=202)
async def sign_in(svc: Services = Depends(get_services)) -> dict:
    """Open Edge, visible, on the browser's profile so you can log in to sites yourself. Aethel's own Edge is
    stopped first: it would be holding that profile."""
    _present(svc)
    _in_use(svc)
    if svc.browser.signing_in():
        raise HTTPException(status_code=409, detail="The sign-in window is already open.")
    if svc.browser.find_edge() is None:
        raise HTTPException(status_code=503, detail="I can't find Microsoft Edge on this computer.")
    await svc.mcp.restart("browser")
    svc.browser.forget()
    svc.browser.open_sign_in()
    return {"opened": True}


@router.post("/restart", status_code=202)
async def restart(svc: Services = Depends(get_services)) -> dict:
    """Restart the browser, e.g. so a changed "Show browser" applies."""
    _present(svc)
    _in_use(svc)
    await svc.mcp.restart("browser")
    svc.browser.forget()
    return {"restarted": True}


@router.delete("/profile", status_code=204)
async def clear_data(svc: Services = Depends(get_services)) -> Response:
    """Forget everything the browser knows: sign-ins, cookies, history, and the pages it saved while working."""
    _in_use(svc)
    if svc.browser.signing_in():
        raise HTTPException(status_code=409, detail="The sign-in window is still open. Close that window first.")
    if "browser" in svc.mcp.status():
        await svc.mcp.restart("browser")  # stops Edge, which holds the profile; it only starts again when it's next used
    svc.browser.forget()
    for attempt in range(CLEAR_ATTEMPTS):
        try:
            shutil.rmtree(svc.browser.profile_dir)
            break
        except FileNotFoundError:
            break
        except OSError:
            await asyncio.sleep(RETRY_PAUSE_S)
    if svc.browser.profile_dir.exists():  # never say "cleared" when it isn't
        raise HTTPException(status_code=409, detail="Some of the browser's files are still in use (Edge may still be "
                                                    "closing). Wait a moment and try again.")
    if svc.browser.out_dir.is_dir():
        for leftover in svc.browser.out_dir.iterdir():
            shutil.rmtree(leftover, ignore_errors=True) if leftover.is_dir() else leftover.unlink(missing_ok=True)
    media = aethel_home() / "media"  # the replay pictures of pages it visited, which may have been signed in
    for rel in svc.tasks.clear_pictures("browser_"):
        path = (media / rel).resolve()
        if path.is_relative_to(media.resolve()):
            path.unlink(missing_ok=True)
    return Response(status_code=204)
