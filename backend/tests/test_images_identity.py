import hashlib
import os

import cv2
import httpx
import numpy as np
import pytest

from aethel.images.identity import SIDE, Face, IdentityCheck, align
from aethel.images.models import FACE_MODELS, ModelError, download, is_installed, model_path

LANDMARKS = np.array([[80, 100], [140, 100], [110, 135], [88, 165], [132, 165]], dtype=np.float32)


def _face_image(colour, shift=0):
    img = np.full((260, 220, 3), 235, np.uint8)
    cv2.ellipse(img, (110 + shift, 130), (70, 95), 0, 0, 360, colour, -1)
    for x, y in LANDMARKS[:2]:
        cv2.circle(img, (int(x) + shift, int(y)), 9, (20, 20, 20), -1)
    cv2.line(img, (88 + shift, 165), (132 + shift, 165), (40, 40, 160), 5)
    return img


class FakeDetector:
    def __init__(self, faces=True, shift=0):
        self.faces, self.shift = faces, shift

    def setInputSize(self, size):
        self.size = size

    def detect(self, image):
        if not self.faces:
            return 1, None
        lm = (LANDMARKS + [self.shift, 0]).ravel()
        return 1, np.array([[40 + self.shift, 35, 140, 190, *lm, 0.95]], dtype=np.float32)


class FakeSession:
    """An "embedding" of the aligned crop: its 8×8 colour thumbnail."""

    class _Input:
        name = "data"

    def get_inputs(self):
        return [self._Input()]

    def run(self, _, feed):
        chw = feed["data"][0]
        assert chw.shape == (3, SIDE, SIDE)
        small = cv2.resize(np.transpose(chw, (1, 2, 0)), (8, 8)).ravel() - 128.0
        return [small[None]]


def _encode(img):
    return cv2.imencode(".jpg", img)[1].tobytes()


def test_alignment_output_is_112():
    crop = align(_face_image((120, 160, 220)), Face((40, 35, 140, 190), LANDMARKS, 0.9))
    assert crop.shape == (SIDE, SIDE, 3)


def test_same_face_scores_higher_than_a_different_one():
    check = IdentityCheck(detector=FakeDetector(), session=FakeSession())
    anchor = check.embedding_of(_encode(_face_image((120, 160, 220))))
    same = check.compare(_encode(_face_image((118, 158, 222))), anchor)
    other = check.compare(_encode(_face_image((40, 200, 60))), anchor)
    assert same > 0.95 and same > other


def test_scenery_gives_none():
    check = IdentityCheck(detector=FakeDetector(faces=False), session=FakeSession())
    assert check.compare(_encode(np.zeros((50, 50, 3), np.uint8)), np.ones(64)) is None
    assert check.compare(b"not an image", np.ones(64)) is None


def test_not_ready_before_the_download(tmp_path):
    ok, why = IdentityCheck(tmp_path).ready()
    assert not ok and "250 MB" in why


def _client(payload):
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=payload)))


def test_download_verifies_the_hash(tmp_path):
    payload = b"model bytes"
    spec = {**FACE_MODELS["yunet"], "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}
    download("yunet", _client(payload), tmp_path, spec=spec)
    assert (tmp_path / spec["file"]).read_bytes() == payload


def test_a_hash_mismatch_is_refused_and_nothing_kept(tmp_path):
    with pytest.raises(ModelError, match="SHA-256"):
        download("yunet", _client(b"tampered"), tmp_path)
    assert not model_path("yunet", tmp_path).exists() and not is_installed("yunet", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_an_oversized_download_is_refused(tmp_path):
    spec = {**FACE_MODELS["yunet"], "size": 3}
    with pytest.raises(ModelError, match="larger"):
        download("yunet", _client(b"too long"), tmp_path, spec=spec)


@pytest.mark.skipif(os.environ.get("AETHEL_IMAGES_IT") != "1", reason="real models: set AETHEL_IMAGES_IT=1")
def test_real_models_load_and_find_no_face_in_a_blank_image():
    check = IdentityCheck()
    if not check.ready()[0]:
        with httpx.Client() as client:
            for name in FACE_MODELS:
                if not is_installed(name):
                    download(name, client)
    assert check.ready()[0]
    assert check.compare(_encode(np.full((200, 200, 3), 200, np.uint8)), np.ones(512) / np.sqrt(512)) is None
