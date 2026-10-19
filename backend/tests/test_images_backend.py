import base64
import json

import httpx
import pytest

from aethel.images.backend import ImageError, ImageRequest, pick_backend
from aethel.images.gemini import GeminiImage

pytestmark = pytest.mark.anyio
PNG = b"\x89PNG fake"


def _gemini(handler, key="k"):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GeminiImage(key=lambda: key, model=lambda: "gemini-3.1-flash-image", http=http)


def _ok(request):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [
        {"text": "here you go"}, {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()}}]}}]})


async def test_request_body_shape_and_parse():
    seen = {}

    def handler(request):
        seen["url"], seen["key"], seen["body"] = str(request.url), request.headers["x-goog-api-key"], json.loads(request.content)
        return _ok(request)

    result = await _gemini(handler).generate(ImageRequest("a beach", [b"anchor", b"sheet"], "landscape"))
    assert seen["url"].endswith("/models/gemini-3.1-flash-image:generateContent") and seen["key"] == "k"
    parts = seen["body"]["contents"][0]["parts"]
    assert parts[0] == {"text": "a beach"}
    assert [base64.b64decode(p["inlineData"]["data"]) for p in parts[1:]] == [b"anchor", b"sheet"]
    assert seen["body"]["generationConfig"]["imageConfig"] == {"aspectRatio": "4:3"}
    assert result.data == PNG and result.mime == "image/png" and result.backend == "gemini"


async def test_snake_case_response_is_read_too():
    data, mime = GeminiImage.parse({"candidates": [{"content": {"parts": [
        {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(b"jpg").decode()}}]}}]})
    assert (data, mime) == (b"jpg", "image/jpeg")


async def test_no_image_is_not_retryable():
    with pytest.raises(ImageError) as err:
        GeminiImage.parse({"candidates": [{"finishReason": "SAFETY", "content": {"parts": [{"text": "no"}]}}]})
    assert "SAFETY" in str(err.value) and not err.value.retryable


@pytest.mark.parametrize("status,retryable", [(429, True), (503, True), (400, False), (403, False)])
async def test_status_codes(status, retryable):
    gem = _gemini(lambda r: httpx.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(ImageError) as err:
        await gem.generate(ImageRequest("x"))
    assert err.value.retryable is retryable


async def test_a_400_about_image_config_retries_without_it():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(400, json={"error": {"message": "Unknown name \"imageConfig\""}})
        return _ok(request)

    assert (await _gemini(handler).generate(ImageRequest("x"))).data == PNG
    assert "imageConfig" in bodies[0]["generationConfig"] and "imageConfig" not in bodies[1]["generationConfig"]


async def test_no_key_is_unavailable():
    ok, why = await _gemini(_ok, key=None).available()
    assert not ok and "Gemini key" in why


class Fake:
    def __init__(self, name, cloud, ok=True):
        self.name, self.cloud, self.ok, self.max_references = name, cloud, ok, 0

    async def available(self):
        return self.ok, ""


async def test_pick_backend_follows_the_order_and_private_mode():
    backends = {"gemini": Fake("gemini", True), "openrouter": Fake("openrouter", True), "local": Fake("local", False)}
    assert (await pick_backend(["gemini", "local"], backends, private=False)).name == "gemini"
    assert (await pick_backend(["gemini", "local"], backends, private=True)).name == "local"
    backends["local"].ok = False
    assert await pick_backend(["gemini", "openrouter", "local"], backends, private=True) is None
    backends["gemini"].ok = False
    assert (await pick_backend(["gemini", "openrouter"], backends, private=False)).name == "openrouter"
