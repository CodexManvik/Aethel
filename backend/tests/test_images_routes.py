import io

from fastapi.testclient import TestClient
from PIL import Image

from aethel.app import create_app
from aethel.images.backend import ImageResult
from aethel.personas.model import Persona
from aethel.services import build_services
from tests.fakes import FakeLocal, FakeProvider, factory_from


def _jpeg(colour) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (60, 80), (235, 235, 235))
    img.paste(Image.new("RGB", (60, 40), colour), (0, 40))
    img.save(buf, "JPEG")
    return buf.getvalue()


class Backend:
    name, cloud, max_references = "fake", True, 6

    def __init__(self):
        self.requests = []

    async def available(self):
        return True, ""

    async def generate(self, req):
        self.requests.append(req)
        return ImageResult(_jpeg((30, 80, 200)), "image/jpeg", "fake", "f", 3)


class Identity:
    def ready(self):
        return True, ""

    def embedding_of(self, data):
        import numpy as np
        return np.ones(4) / 2

    def compare(self, data, anchor):
        return 0.8


def _client():
    svc = build_services(provider_factory=factory_from({"groq:g": FakeProvider()}), local_llm=FakeLocal(up=False))
    backend = Backend()
    svc.images.backends = {"gemini": backend}
    svc.images.identity = Identity()
    svc.personas.create(Persona(id="x", name="Mira", image_style="film photo"))
    return TestClient(create_app(svc)), svc, backend


def test_portraits_pick_an_anchor_then_make_the_sheet():
    client, svc, backend = _client()
    with client:
        faces = client.post("/api/personas/mira/portraits").json()
        assert len(faces) == 4 and all(f["ok"] for f in faces)
        assert all(r.references == [] for r in backend.requests)  # faces from the style alone
        picked = client.post("/api/personas/mira/anchor", json={"image_id": faces[2]["id"]}).json()
        assert picked["anchors"] == ["portraits/anchor.jpg"] and picked["sheet"] == []
        assert picked["accent"]["source"] == "anchor"  # blue from the picture, not the grey background
        assert (svc.personas.folder("mira") / "anchor.npy").exists()
        backend.requests.clear()
        sheet = client.post("/api/personas/mira/sheet").json()
        assert sheet["persona"]["sheet"] == ["portraits/sheet-34.jpg", "portraits/sheet-profile.jpg",
                                             "portraits/sheet-smile.jpg", "portraits/sheet-laugh.jpg"]
        assert all(len(r.references) == 1 for r in backend.requests)  # the anchor only
        assert client.get("/api/personas/mira/files/portraits/sheet-34.jpg").status_code == 200
        studio = client.get("/api/personas/mira/gallery").json()
        assert len(studio) == 8 and {g["reason"] for g in studio} == {"studio"}


def test_anchor_must_be_a_gallery_photo_and_the_sheet_needs_a_face():
    client, _, _ = _client()
    with client:
        assert client.post("/api/personas/mira/anchor", json={"image_id": "../persona"}).status_code == 404
        assert client.post("/api/personas/mira/sheet").status_code == 409
        assert client.post("/api/personas/nobody/portraits").status_code == 404


def test_status_says_why_there_is_no_backend(monkeypatch):
    client, svc, _ = _client()
    with client:
        ok = client.get("/api/images/status").json()
        assert ok["backend"] == "fake" and ok["face_models"] is False and ok["progress"] == 0
        svc.settings.update({"private_mode": True})
        private = client.get("/api/images/status").json()
        assert private["backend"] is None and "private mode" in private["why_not"]


def test_reference_toggle_and_retry_routes():
    client, svc, _ = _client()
    with client:
        face = client.post("/api/personas/mira/portraits").json()[0]
        on = client.post(f"/api/personas/mira/gallery/{face['id']}/reference", json={"on": True}).json()
        assert on["reference"] is True
        assert client.post("/api/personas/mira/gallery/nope/reference", json={"on": True}).status_code == 404
        assert client.post("/api/images/unknown/retry").status_code == 404
