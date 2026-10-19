"""Any server with the OpenAI images API: OpenAI itself, LocalAI, and the many proxies and hosted APIs that copy it.
With reference images it asks /images/edits (multipart, the references as image[]), which is how a face is kept;
without, /images/generations. The picture comes back as b64_json, or as a url that is then fetched."""
import base64
import time
from typing import Callable

import httpx

from .backend import ImageError, ImageRequest, ImageResult, is_loopback

SIZES = {"portrait": "1024x1536", "square": "1024x1024", "landscape": "1536x1024"}
TIMEOUT_S = 180.0


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
            try:
                got = await self.http.get(item["url"], timeout=60)
                got.raise_for_status()
            except httpx.HTTPError as exc:
                raise ImageError("The picture's link couldn't be fetched.", retryable=True) from exc
            return got.content, got.headers.get("content-type", "image/png").split(";")[0]
        raise ImageError("The image server didn't return a picture.")


def _message(r: httpx.Response) -> str:
    try:
        err = r.json().get("error")
        return str(err.get("message") if isinstance(err, dict) else err or "")[:200]
    except ValueError:
        return r.text[:200]
