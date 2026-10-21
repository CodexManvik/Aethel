import hashlib
import io
import subprocess
import time
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from aethel import downloads as dl
from aethel.app import create_app
from aethel.images.models import ModelError
from aethel.services import build_services
from tests.fakes import FakeLocal, FakeProvider, factory_from


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def _wait(downloads, name):
    for _ in range(200):
        if not downloads.jobs[name].running:
            return
        time.sleep(0.02)
    raise AssertionError("still running")


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "MODELS_DIR", tmp_path / "models")
    s = build_services(provider_factory=factory_from({"g:g": FakeProvider()}), local_llm=FakeLocal(up=False))
    return s


def test_safe_extract_refuses_a_zip_slip(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(_zip({"../escape.txt": b"x"}))
    with pytest.raises(ModelError, match="unsafe"):
        dl.safe_extract(bad, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


def test_llama_server_download_extracts_and_is_used(svc, monkeypatch):
    archive = _zip({"llama-server.exe": b"MZ", "ggml-vulkan.dll": b"x"})
    monkeypatch.setitem(dl.LLAMA, "vulkan", {"file": "llama.zip", "size": len(archive),
                                             "sha256": hashlib.sha256(archive).hexdigest()})
    downloads = dl.Downloads(settings=svc.settings, client_factory=lambda: httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=archive))))
    assert downloads.installed("llama-vulkan") is False
    assert downloads.start("llama-vulkan") is True
    _wait(downloads, "llama-vulkan")
    assert downloads.jobs["llama-vulkan"].error is None and downloads.installed("llama-vulkan")
    server = svc.settings.get().local_llm.server_path
    assert server.endswith("llama-server.exe") and dl.LLAMA_TAG in server


def test_a_tampered_download_is_refused(svc, monkeypatch):
    downloads = dl.Downloads(settings=svc.settings, client_factory=lambda: httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"evil"))))
    downloads.start("llama-cpu")
    _wait(downloads, "llama-cpu")
    assert "SHA-256" in downloads.jobs["llama-cpu"].error and not downloads.installed("llama-cpu")
    assert svc.settings.get().local_llm.server_path == ""


def test_voices_download_both_files(svc, monkeypatch, tmp_path):
    files = {"kokoro-v1.0.onnx": b"onnx", "voices-v1.0.bin": b"voices"}
    monkeypatch.setattr(dl, "KOKORO", [{"file": n, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()}
                                       for n, b in files.items()])
    downloads = dl.Downloads(settings=svc.settings, client_factory=lambda: httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=files[r.url.path.split("/")[-1]]))))
    downloads.start("voices")
    _wait(downloads, "voices")
    assert downloads.installed("voices") and (tmp_path / "models" / "tts" / "voices-v1.0.bin").read_bytes() == b"voices"
    assert downloads.status()["voices"]["progress"] == 1.0


def test_local_images_only_in_the_installed_app(svc, monkeypatch, tmp_path):
    monkeypatch.delenv("AETHEL_UV", raising=False)
    downloads = dl.Downloads(settings=svc.settings)
    with pytest.raises(ModelError, match="pip install -r backend/requirements/local-images.txt"):
        downloads.start("local-images")
    calls = []

    def runner(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setenv("AETHEL_UV", "C:/app/uv/uv.exe")
    monkeypatch.setenv("AETHEL_RESOURCES", str(tmp_path / "res"))
    downloads = dl.Downloads(settings=svc.settings, runner=runner)
    assert downloads.status()["packages_installable"] is True
    downloads.start("local-images")
    _wait(downloads, "local-images")
    assert calls[0][:3] == ["C:/app/uv/uv.exe", "pip", "install"] and calls[0][-1].endswith("local-images.txt")
    assert downloads.jobs["local-images"].error is None


def test_routes(svc):
    with TestClient(create_app(svc)) as client:
        status = client.get("/api/downloads").json()
        assert set(status) >= {"voices", "llama-vulkan", "llama-cpu", "local-images", "packages_installable"}
        assert client.post("/api/downloads/nothing").status_code == 404
        svc.settings.update({"private_mode": True})
        assert client.post("/api/downloads/voices").status_code == 409
