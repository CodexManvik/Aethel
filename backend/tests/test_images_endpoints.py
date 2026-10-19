import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from aethel.app import create_app
from aethel.images.backend import ImageError, ImageRequest, is_loopback, pick_backend
from aethel.images.comfyui import ComfyUI, WorkflowError, fill, load_workflow, placeholders
from aethel.images.local.zimage import LocalZImage
from aethel.images.openai_images import OpenAIImages
from aethel.services import build_services
from aethel.settings import AppSettings
from tests.fakes import FakeLocal, FakeProvider, factory_from

pytestmark = pytest.mark.anyio
PNG = b"\x89PNG fake"


def test_loopback():
    assert is_loopback("http://127.0.0.1:8188") and is_loopback("http://localhost:7860/v1")
    assert is_loopback("http://[::1]:8188") and is_loopback("http://127.5.0.1")
    assert not is_loopback("http://192.168.1.20:8188") and not is_loopback("https://api.openai.com/v1")
    assert not is_loopback("http://localhost.evil.com") and not is_loopback("")


def _openai(handler, base="https://api.example.com/v1", key="sk"):
    return OpenAIImages(base_url=lambda: base, model=lambda: "gpt-image-1", key=lambda: key,
                        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_openai_generation_without_references():
    seen = {}

    def handler(r):
        seen["url"], seen["auth"], seen["body"] = str(r.url), r.headers.get("authorization"), json.loads(r.content)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})

    got = await _openai(handler).generate(ImageRequest("a café", [], "landscape"))
    assert seen["url"].endswith("/images/generations") and seen["auth"] == "Bearer sk"
    assert seen["body"] == {"model": "gpt-image-1", "prompt": "a café", "size": "1536x1024", "n": 1}
    assert got.data == PNG and got.backend == "openai"


async def test_openai_edits_with_references_and_a_url_answer():
    seen = {}

    def handler(r):
        if r.url.path.endswith("/images/edits"):
            seen["type"], seen["body"] = r.headers["content-type"], r.content
            return httpx.Response(200, json={"data": [{"url": "https://api.example.com/files/x.png"}]})
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    got = await _openai(handler, key=None).generate(ImageRequest("beach", [b"ANCHOR", b"SHEET"]))
    assert seen["type"].startswith("multipart/form-data")
    assert seen["body"].count(b'name="image[]"') == 2 and b"ANCHOR" in seen["body"] and b"1024x1536" in seen["body"]
    assert got.data == PNG


async def test_a_picture_link_to_another_site_is_not_followed():
    def handler(r):
        return httpx.Response(200, json={"data": [{"url": "http://169.254.169.254/latest"}]})
    with pytest.raises(ImageError, match="another site"):
        await _openai(handler).generate(ImageRequest("x"))


@pytest.mark.parametrize("status,retryable", [(429, True), (500, True), (400, False)])
async def test_openai_errors(status, retryable):
    with pytest.raises(ImageError) as err:
        await _openai(lambda r: httpx.Response(status, json={"error": {"message": "no"}})).generate(ImageRequest("x"))
    assert err.value.retryable is retryable


async def test_openai_availability_and_locality():
    off = OpenAIImages(base_url=lambda: "", model=lambda: "m", key=lambda: None, http=httpx.AsyncClient())
    assert (await off.available())[0] is False
    assert _openai(lambda r: None, base="http://127.0.0.1:8080/v1").cloud is False
    assert _openai(lambda r: None).cloud is True


WORKFLOW = {
    "3": {"class_type": "KSampler", "inputs": {"seed": "{{seed}}", "steps": 8}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": "{{width}}", "height": "{{height}}"}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "masterpiece, {{prompt}}"}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{negative}}"}},
    "10": {"class_type": "LoadImage", "inputs": {"image": "{{reference}}"}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}},
}


def _workflow(tmp_path, data=WORKFLOW):
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_workflow_checks(tmp_path):
    assert placeholders(load_workflow(_workflow(tmp_path))) == ["prompt", "negative", "seed", "width", "height", "reference"]
    for bad, why in [({"a": 1}, "API-format"), ({"1": {"class_type": "X", "inputs": {}}}, "{{prompt}}")]:
        with pytest.raises(WorkflowError, match=why.replace("{", r"\{").replace("}", r"\}")):
            load_workflow(_workflow(tmp_path, bad))
    (tmp_path / "bad.json").write_text("not json")
    with pytest.raises(WorkflowError, match="isn't JSON"):
        load_workflow(str(tmp_path / "bad.json"))
    with pytest.raises(WorkflowError):
        load_workflow("")


def test_fill_keeps_numbers_numeric():
    filled = fill(WORKFLOW, {"prompt": "a cat", "negative": "ugly", "seed": 42, "width": 832, "height": 1216,
                             "reference": "ref.jpg"})
    assert filled["3"]["inputs"]["seed"] == 42 and filled["5"]["inputs"] == {"width": 832, "height": 1216}
    assert filled["6"]["inputs"]["text"] == "masterpiece, a cat" and filled["10"]["inputs"]["image"] == "ref.jpg"
    assert WORKFLOW["6"]["inputs"]["text"] == "masterpiece, {{prompt}}"  # the original isn't touched


async def test_comfyui_upload_prompt_history_view(tmp_path):
    calls, polls = [], []

    def handler(r):
        calls.append(f"{r.method} {r.url.path}")
        if r.url.path == "/upload/image":
            return httpx.Response(200, json={"name": "aethel-1.jpg", "subfolder": "", "type": "input"})
        if r.url.path == "/prompt":
            sent = json.loads(r.content)["prompt"]
            assert sent["10"]["inputs"]["image"] == "aethel-1.jpg" and "beach" in sent["6"]["inputs"]["text"]
            return httpx.Response(200, json={"prompt_id": "p1"})
        if r.url.path == "/history/p1":
            polls.append(1)
            if len(polls) < 2:
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"p1": {"status": {"completed": True}, "outputs": {"9": {"images": [
                {"filename": "out.png", "subfolder": "", "type": "output"}]}}}})
        if r.url.path == "/view":
            assert r.url.params["filename"] == "out.png"
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        return httpx.Response(404)

    async def no_sleep(_):
        pass

    comfy = ComfyUI(base_url=lambda: "http://127.0.0.1:8188", workflow_path=lambda: _workflow(tmp_path),
                    http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sleep=no_sleep)
    assert comfy.max_references == 1 and comfy.cloud is False and (await comfy.available())[0]
    got = await comfy.generate(ImageRequest("on the beach", [b"anchor"]))
    assert got.data == PNG and calls[:2] == ["POST /upload/image", "POST /prompt"] and calls[-1] == "GET /view"


async def test_comfyui_reports_a_failed_run_and_a_missing_face(tmp_path):
    def handler(r):
        if r.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p1"})
        return httpx.Response(200, json={"p1": {"status": {"status_str": "error"}, "outputs": {}}})

    comfy = ComfyUI(base_url=lambda: "http://10.0.0.5:8188", workflow_path=lambda: _workflow(tmp_path),
                    http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert comfy.cloud is True
    with pytest.raises(ImageError, match="no face yet"):
        await comfy.generate(ImageRequest("x", []))
    text_only = {k: v for k, v in WORKFLOW.items() if k != "10"}
    comfy = ComfyUI(base_url=lambda: "http://10.0.0.5:8188", workflow_path=lambda: _workflow(tmp_path, text_only),
                    http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert comfy.max_references == 0
    with pytest.raises(ImageError, match="failed while running"):
        await comfy.generate(ImageRequest("x", []))


async def test_private_mode_uses_only_servers_on_this_pc(tmp_path):
    lan = OpenAIImages(base_url=lambda: "http://192.168.1.9:8080/v1", model=lambda: "m", key=lambda: None,
                       http=httpx.AsyncClient())
    here = ComfyUI(base_url=lambda: "http://127.0.0.1:8188", workflow_path=lambda: _workflow(tmp_path),
                   http=httpx.AsyncClient())
    assert await pick_backend(["openai", "comfyui"], {"openai": lan, "comfyui": here}, private=True) is here
    assert await pick_backend(["openai"], {"openai": lan}, private=True) is None


async def test_the_local_folder_comes_from_settings(tmp_path):
    folder = tmp_path / "D" / "zimage"
    folder.mkdir(parents=True)
    chosen = {"dir": ""}
    local = LocalZImage(image_dir=lambda: chosen["dir"])
    assert local.image_dir.name == "image"  # the default models/image
    chosen["dir"] = str(folder)
    assert (await local.available()) == (False, f"No Z-Image model in {folder}.")
    (folder / "z-image-turbo.safetensors").write_bytes(b"x")
    assert (await local.available())[0] is True


def test_comfyui_check_route(tmp_path):
    svc = build_services(provider_factory=factory_from({"g:g": FakeProvider()}), local_llm=FakeLocal(up=False))
    with TestClient(create_app(svc)) as client:
        ok = client.post("/api/images/comfyui/check", json={"path": _workflow(tmp_path)}).json()
        assert ok["ok"] and "reference" in ok["placeholders"]
        bad = client.post("/api/images/comfyui/check", json={"path": str(tmp_path / "missing.json")}).json()
        assert bad["ok"] is False and "can't be read" in bad["error"]


def test_images_openai_is_a_reserved_endpoint_id():
    with pytest.raises(ValueError):
        AppSettings.model_validate({"endpoints": [{"id": "images-openai", "name": "x", "base_url": ""}]})
