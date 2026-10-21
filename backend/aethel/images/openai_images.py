"""Any server with the OpenAI images API: OpenAI itself, LocalAI, and the many proxies and hosted APIs that copy it.
With reference images it asks /images/edits (multipart, the references as image[]), which is how a face is kept;
without, /images/generations. The picture comes back as b64_json, or as a url that is then fetched."""
import base64
import time
from typing import Callable
from urllib.parse import urlsplit

import httpx

from .backend import ImageError, ImageRequest, ImageResult, is_loopback

SIZES = {"portrait": "1024x1536", "square": "1024x1024", "landscape": "1536x1024"}
TIMEOUT_S = 180.0
MAX_PICTURE_BYTES = 40 * 1024 * 1024


class OpenAIImages:
    max_references = 6

    def __init__(self, *, base_url: Callable[[], str], model: Callable[[], str], key: Callable[[], str | None],
                 http: httpx.AsyncClient):
        self._base, self._model, self._key, self.http = base_url, model, key, http

    @property
    def name(self) -> str:
        return f"openai:{self._model()}"

    @property
    def cloud(self) -> bool:
        return not is_loopback(self._base())

    async def available(self) -> tuple[bool, str]:
        if not self._base() or not self._model():
            return False, "Set the OpenAI-compatible image server's URL and model in Settings → Images."
        return True, ""

    def _headers(self) -> dict:
        key = self._key()
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def generate(self, req: ImageRequest) -> ImageResult:
        base, model = self._base().rstrip("/"), self._model()
        if not base or not model:
            raise ImageError("The OpenAI-compatible image server isn't set up.")
        started = time.monotonic()
        try:
            if req.references:
                field = "image[]" if len(req.references) > 1 else "image"
                files = [(field, (f"reference-{i}.jpg", ref, "image/jpeg")) for i, ref in enumerate(req.references)]
                r = await self.http.post(f"{base}/images/edits", headers=self._headers(), files=files,
                                         data={"model": model, "prompt": req.prompt, "size": SIZES[req.size], "n": "1"},
                                         timeout=TIMEOUT_S)
            else:
                r = await self.http.post(f"{base}/images/generations", headers=self._headers(), timeout=TIMEOUT_S,
                                         json={"model": model, "prompt": req.prompt, "size": SIZES[req.size], "n": 1})
        except httpx.HTTPError as exc:
            raise ImageError(f"Couldn't reach the image server: {type(exc).__name__}", retryable=True) from exc
        if r.status_code == 429 or r.status_code >= 500:
            raise ImageError(f"The image server is busy ({r.status_code}).", retryable=True)
        if r.status_code >= 400:
            raise ImageError(f"The image server refused the request ({r.status_code}): {_message(r)}")
        data, mime = await self._picture(r.json())
        return ImageResult(data=data, mime=mime, backend="openai", model=model,
                           latency_ms=int((time.monotonic() - started) * 1000))

    async def _picture(self, body: dict) -> tuple[bytes, str]:
        item = (body.get("data") or [{}])[0] or {}
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"]), "image/png"
        if item.get("url"):
            # Only from the server that was asked: a link elsewhere could make Aethel fetch anything (and, from a
            # server on this PC, take a private-mode request off the machine).
            if _origin(item["url"]) != _origin(self._base()):
                raise ImageError("The image server answered with a link to another site; it wasn't followed.")
            try:
                async with self.http.stream("GET", item["url"], timeout=60) as got:
                    got.raise_for_status()
                    data = bytearray()
                    async for chunk in got.aiter_bytes():
                        data += chunk
                        if len(data) > MAX_PICTURE_BYTES:
                            raise ImageError("The picture was too large.")
                    mime = got.headers.get("content-type", "image/png").split(";")[0]
            except httpx.HTTPError as exc:
                raise ImageError("The picture's link couldn't be fetched.", retryable=True) from exc
            return bytes(data), mime
        raise ImageError("The image server didn't return a picture.")


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port


def _message(r: httpx.Response) -> str:
    try:
        err = r.json().get("error")
        return str(err.get("message") if isinstance(err, dict) else err or "")[:200]
    except ValueError:
        return r.text[:200]
