import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from PIL import Image

from aethel.images.backend import ImageError, ImageRequest
from aethel.images.local.zimage import LocalZImage, find_components
from aethel.images.openrouter import OpenRouterImage

pytestmark = pytest.mark.anyio


async def test_openrouter_body_and_parse():
    seen = {}

    def handler(request):
        seen["auth"], seen["body"] = request.headers["authorization"], json.loads(request.content)
        url = "data:image/png;base64," + base64.b64encode(b"png").decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "", "images": [
            {"type": "image_url", "image_url": {"url": url}}]}}]})

    backend = OpenRouterImage(key=lambda: "k", model=lambda: "google/gemini-3.1-flash-image",
                              http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await backend.generate(ImageRequest("a café", [b"ref"], "square"))
    assert seen["auth"] == "Bearer k" and seen["body"]["modalities"] == ["image", "text"]
    content = seen["body"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "a café"}
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(b"ref").decode()
    assert (result.data, result.mime, result.backend) == (b"png", "image/png", "openrouter")


async def test_openrouter_is_off_without_a_model():
    backend = OpenRouterImage(key=lambda: "k", model=lambda: "", http=httpx.AsyncClient())
    assert (await backend.available())[0] is False
    with pytest.raises(ImageError):
        OpenRouterImage.parse({"choices": [{"message": {"content": "sorry"}}]})


async def test_local_unavailable_without_weights(tmp_path):
    assert (await LocalZImage(image_dir=tmp_path).available())[0] is False
    assert find_components(tmp_path) is None
    (tmp_path / "z-image-turbo-Q4.gguf").write_bytes(b"x")
    (tmp_path / "ae.safetensors").write_bytes(b"x")
    assert find_components(tmp_path) == {"transformer": str(tmp_path / "z-image-turbo-Q4.gguf"),
                                         "vae": str(tmp_path / "ae.safetensors"), "text_encoder": None}


class FakeLlama:
    def __init__(self):
        self.calls = []

    def suspend(self):
        self.calls.append("suspend")
        return True

    def resume(self):
        self.calls.append("resume")
        return True


class FakePipe:
    def __init__(self, fail=False):
        self.fail, self.prompts = fail, []

    def __call__(self, prompt, width, height, **kw):
        self.prompts.append((prompt, width, height))
        if self.fail:
            raise RuntimeError("CUDA out of memory")
        return type("Out", (), {"images": [Image.new("RGB", (width, height), (90, 120, 200))]})()


def _local(tmp_path, pipe, small_gpu=True):
    (tmp_path / "z-image-turbo.safetensors").write_bytes(b"x")
    llama = FakeLlama()
    return LocalZImage(image_dir=tmp_path, local_llm=llama, loader=lambda comps, d: pipe,
                       small_gpu=lambda: small_gpu), llama


async def test_handoff_suspends_and_resumes_around_generation(tmp_path):
    pipe = FakePipe()
    local, llama = _local(tmp_path, pipe)
    result = await local.generate(ImageRequest("a beach", size="portrait"))
    assert llama.calls == ["suspend", "resume"] and pipe.prompts == [("a beach", 896, 1152)]
    assert result.mime == "image/jpeg" and result.data[:2] == b"\xff\xd8"


async def test_resume_runs_even_when_generation_fails(tmp_path):
    local, llama = _local(tmp_path, FakePipe(fail=True))
    with pytest.raises(ImageError):
        await local.generate(ImageRequest("x"))
    assert llama.calls == ["suspend", "resume"]


async def test_no_handoff_on_a_big_gpu(tmp_path):
    local, llama = _local(tmp_path, FakePipe(), small_gpu=False)
    await local.generate(ImageRequest("x"))
    assert llama.calls == []


def test_importing_the_local_backend_loads_no_torch():
    code = "import sys; import aethel.images.local.zimage; print('torch' in sys.modules, 'diffusers' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])})
    assert out.stdout.strip() == "False False", out.stderr
