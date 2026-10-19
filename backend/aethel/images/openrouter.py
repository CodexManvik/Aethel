"""OpenRouter's image-output models through chat completions with modalities ["image", "text"]: reference images go
as image_url parts, and the picture comes back as a data URL in choices[0].message.images. Off until the user picks a
model in Settings -> Images."""
import base64
import re
import time
from typing import Callable

import httpx

from .backend import ASPECT, ImageError, ImageRequest, ImageResult

URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_S = 120.0
_DATA_URL = re.compile(r"^data:(image/[a-z+.-]+);base64,(.+)$", re.S)


class OpenRouterImage:
    cloud = True
    max_references = 6

    def __init__(self, *, key: Callable[[], str | None], model: Callable[[], str], http: httpx.AsyncClient):
        self._key, self._model, self.http = key, model, http

    @property
    def name(self) -> str:
        return f"openrouter:{self._model()}"

    async def available(self) -> tuple[bool, str]:
        if not self._model():
            return False, "Pick an OpenRouter image model in Settings → Images."
        if not self._key():
            return False, "Add an OpenRouter key in Settings → Providers."
        return True, ""

    def body(self, req: ImageRequest) -> dict:
        content = [{"type": "text", "text": req.prompt}]
        content += [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(r).decode()}}
                    for r in req.references]
        return {"model": self._model(), "modalities": ["image", "text"],
                "messages": [{"role": "user", "content": content}],
                "image_config": {"aspect_ratio": ASPECT[req.size]}}

    @staticmethod
    def parse(data: dict) -> tuple[bytes, str]:
        for choice in data.get("choices") or []:
            for image in (choice.get("message") or {}).get("images") or []:
                m = _DATA_URL.match(((image or {}).get("image_url") or {}).get("url") or "")
                if m:
                    return base64.b64decode(m.group(2)), m.group(1)
        raise ImageError("The image model didn't return a picture.", retryable=False)

    async def generate(self, req: ImageRequest) -> ImageResult:
        key, model = self._key(), self._model()
        if not key or not model:
            raise ImageError("OpenRouter images aren't set up.", retryable=False)
        started = time.monotonic()
        try:
            r = await self.http.post(URL, json=self.body(req), headers={"Authorization": f"Bearer {key}"},
                                     timeout=TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise ImageError(f"Couldn't reach OpenRouter: {type(exc).__name__}", retryable=True) from exc
        if r.status_code == 429 or r.status_code >= 500:
            raise ImageError(f"OpenRouter is busy ({r.status_code}).", retryable=True)
        if r.status_code >= 400:
            raise ImageError(f"OpenRouter refused the request ({r.status_code}).", retryable=False)
        data, mime = self.parse(r.json())
        return ImageResult(data=data, mime=mime, backend="openrouter", model=model,
                           latency_ms=int((time.monotonic() - started) * 1000))
