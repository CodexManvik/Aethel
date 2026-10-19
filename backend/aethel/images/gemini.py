"""Gemini's native image models through generateContent (D4: reference images are what keep a face the same).

Checked 2026-10-19 against ai.google.dev/gemini-api/docs/image-generation (spec §10.2, plan 5b Task B1). The page
now leads with the newer Interactions API (POST /v1beta/interactions); generateContent is still listed beside it in
the API reference with no deprecation notice, and it's what this uses:
- model ids on the page: gemini-nano-banana-2.1 (recommended for new projects), gemini-3.1-flash-image,
  gemini-3.1-flash-lite-image, gemini-3-pro-image, gemini-2.5-flash-image (legacy). Pinned default:
  gemini-3.1-flash-image (ImageSettings.gemini_model); Settings -> Images lists what the user's key can use.
- up to 14 input images per request (per-model caps for objects 6–14); Aethel sends at most 6 (anchor + sheet + pool).
- aspect ratios include 1:1, 3:4, 4:3; sizes 1K/2K/4K (uppercase K). Under generateContent they go in
  generationConfig.imageConfig {aspectRatio, imageSize} (third-party docs and Google's forum; Google's page only
  shows the Interactions form, response_format.aspect_ratio). Not yet verified live: no key was reachable outside
  the app. A 400 that names imageConfig is retried once without it.
- every image carries a SynthID watermark.
The REST API accepts camelCase (inlineData, mimeType); the response is read in either case."""
import base64
import time
from typing import Callable

import httpx

from .backend import ASPECT, ImageError, ImageRequest, ImageResult

BASE = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT_S = 90.0


class GeminiImage:
    cloud = True
    max_references = 6

    def __init__(self, *, key: Callable[[], str | None], model: Callable[[], str], http: httpx.AsyncClient):
        self._key, self._model, self.http = key, model, http

    @property
    def name(self) -> str:
        return f"gemini:{self._model()}"

    async def available(self) -> tuple[bool, str]:
        if not self._key():
            return False, "Add a Gemini key in Settings → Providers to make photos."
        return True, ""

    @staticmethod
    def body(req: ImageRequest, image_config: bool = True) -> dict:
        parts = [{"text": req.prompt}]
        parts += [{"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(r).decode()}} for r in req.references]
        config: dict = {"responseModalities": ["TEXT", "IMAGE"]}
        if image_config:
            config["imageConfig"] = {"aspectRatio": ASPECT[req.size]}
        return {"contents": [{"role": "user", "parts": parts}], "generationConfig": config}

    @staticmethod
    def parse(data: dict) -> tuple[bytes, str]:
        for cand in data.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                blob = part.get("inlineData") or part.get("inline_data")
                if blob and blob.get("data"):
                    return base64.b64decode(blob["data"]), blob.get("mimeType") or blob.get("mime_type") or "image/png"
        reason = ((data.get("candidates") or [{}])[0].get("finishReason") or
                  (data.get("promptFeedback") or {}).get("blockReason") or "no image")
        raise ImageError(f"The image model didn't return a picture ({reason}).", retryable=False)

    async def generate(self, req: ImageRequest) -> ImageResult:
        key = self._key()
        if not key:
            raise ImageError("No Gemini key.", retryable=False)
        model = self._model()
        started = time.monotonic()
        image_config = True
        for _ in range(2):
            try:
                r = await self.http.post(f"{BASE}/models/{model}:generateContent", json=self.body(req, image_config),
                                         headers={"x-goog-api-key": key}, timeout=TIMEOUT_S)
            except httpx.HTTPError as exc:
                raise ImageError(f"Couldn't reach Gemini: {type(exc).__name__}", retryable=True) from exc
            if r.status_code == 400 and image_config and "imageConfig" in r.text:
                image_config = False  # an older model without imageConfig: the prompt's framing still applies
                continue
            break
        if r.status_code == 429 or r.status_code >= 500:
            raise ImageError(f"Gemini is busy ({r.status_code}).", retryable=True)
        if r.status_code >= 400:
            raise ImageError(f"Gemini refused the request ({r.status_code}): {_message(r)}", retryable=False)
        data, mime = self.parse(r.json())
        return ImageResult(data=data, mime=mime, backend="gemini", model=model,
                           latency_ms=int((time.monotonic() - started) * 1000))

    async def models(self) -> list[str]:
        """The image-capable model ids this key can use (a free call), for Settings -> Images."""
        key = self._key()
        if not key:
            return []
        r = await self.http.get(f"{BASE}/models", params={"pageSize": 1000}, headers={"x-goog-api-key": key},
                                timeout=20)
        r.raise_for_status()
        return sorted(m["name"].removeprefix("models/") for m in r.json().get("models", [])
                      if "image" in m["name"] and "generateContent" in (m.get("supportedGenerationMethods") or []))


def _message(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error", {}).get("message", ""))[:200]
    except ValueError:
        return r.text[:200]
