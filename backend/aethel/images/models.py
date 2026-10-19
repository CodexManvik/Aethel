"""The face models, downloaded on first use from pinned commits, verified against pinned SHA-256 hashes (D5).
Their licences are recorded in docs/third_party.md."""
import hashlib
import logging
import os
from pathlib import Path
from typing import Callable

import httpx

from ..paths import aethel_home

log = logging.getLogger("aethel.images")
FACE_MODELS = {
    "yunet": {
        "file": "face_detection_yunet_2023mar.onnx",
        "url": "https://media.githubusercontent.com/media/opencv/opencv_zoo/f12e12798e8314f7c074a6656816c048dcc95b7a/"
               "models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "sha256": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        "size": 232_589,
        "license": "MIT",
    },
    "arcface": {
        "file": "arcfaceresnet100-8.onnx",
        "url": "https://media.githubusercontent.com/media/onnx/models/4c46cd00fbdb7cd30b6c1c17ab54f2e1f4f7b177/"
               "validated/vision/body_analysis/arcface/model/arcfaceresnet100-8.onnx",
        "sha256": "f3a6bc281e72f88862f5748b53be3d76b3b48f8f1ab1f4a537941bdc4e1b01da",
        "size": 261_036_388,
        "license": "Apache-2.0",
    },
}
VERIFIED = ".verified"  # "<sha256>" written next to a file once its hash checked out


class ModelError(RuntimeError):
    pass


def face_dir() -> Path:
    return Path(os.environ.get("AETHEL_FACE_DIR", aethel_home() / "models" / "face"))


def model_path(name: str, root: Path | None = None) -> Path:
    return (root or face_dir()) / FACE_MODELS[name]["file"]


def is_installed(name: str, root: Path | None = None) -> bool:
    """Present and verified (the hash is checked once, at download, not on every start: the file is 250 MB)."""
    path = model_path(name, root)
    marker = path.with_name(path.name + VERIFIED)
    return path.is_file() and marker.is_file() and marker.read_text().strip() == FACE_MODELS[name]["sha256"]


def download(name: str, client: httpx.Client, root: Path | None = None,
             progress: Callable[[int, int], None] | None = None, spec: dict | None = None) -> Path:
    """Blocking. Streams to a temporary file, checks the size and SHA-256, then moves it into place; a mismatch is
    refused and nothing is kept."""
    spec = spec or FACE_MODELS[name]
    path = (root or face_dir()) / spec["file"]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    digest, done = hashlib.sha256(), 0
    try:
        with client.stream("GET", spec["url"], follow_redirects=True, timeout=60) as r:
            if r.status_code != 200:
                raise ModelError(f"Downloading {spec['file']} failed ({r.status_code}).")
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    done += len(chunk)
                    if done > spec["size"]:
                        raise ModelError(f"{spec['file']} is larger than expected.")
                    digest.update(chunk)
                    f.write(chunk)
                    if progress:
                        progress(done, spec["size"])
        if digest.hexdigest() != spec["sha256"]:
            raise ModelError(f"{spec['file']} didn't match its pinned SHA-256; refused.")
        os.replace(tmp, path)
        path.with_name(path.name + VERIFIED).write_text(spec["sha256"])
        log.info("downloaded and verified %s", spec["file"])
        return path
    finally:
        tmp.unlink(missing_ok=True)
