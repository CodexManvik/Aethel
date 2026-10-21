"""Things the installed app fetches on request (spec 2026-10-19 §I3), each pinned to a release and a SHA-256 and
recorded in docs/third_party.md: Kokoro's voices, a llama.cpp server, and the optional packages for local images.
One download at a time per item, in a thread; the Settings sections poll /api/downloads."""
import importlib.util
import logging
import os
import subprocess
import sys
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from .images.models import ModelError, download
from .paths import MODELS_DIR, aethel_home

log = logging.getLogger("aethel.downloads")
LLAMA_TAG = "b11381"  # 2026-10-03
LLAMA = {
    "vulkan": {"file": f"llama-{LLAMA_TAG}-bin-win-vulkan-x64.zip", "size": 33_287_351,
               "sha256": "517032b9ab2e1c7991a1313fa8c39cbc1a9da6ebf82944cf34ccf6adf3d0a37a"},
    "cpu": {"file": f"llama-{LLAMA_TAG}-bin-win-cpu-x64.zip", "size": 19_367_262,
            "sha256": "f5cb0430a9dd2174f392fdad17ed5d6417e5542c47efc993fa4917287a738a76"},
}
KOKORO = [
    {"file": "kokoro-v1.0.onnx", "size": 325_532_387,
     "sha256": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"},
    {"file": "voices-v1.0.bin", "size": 28_214_398,
     "sha256": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"},
]
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
LLAMA_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/"


def tts_dir() -> Path:
    return MODELS_DIR / "tts"


def llama_dir(flavor: str) -> Path:
    return aethel_home() / "bin" / f"llama-{LLAMA_TAG}-{flavor}"


def safe_extract(archive: Path, dest: Path) -> None:
    """Unzips, refusing any entry that would land outside `dest` (a "zip slip")."""
    dest = dest.resolve()
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            target = (dest / member.filename).resolve()
            if not target.is_relative_to(dest):
                raise ModelError(f"The archive has an unsafe path: {member.filename}")
        z.extractall(dest)


def find_server(folder: Path) -> Path | None:
    hits = sorted(folder.rglob("llama-server.exe")) if folder.is_dir() else []
    return hits[0] if hits else None


@dataclass
class Job:
    running: bool = False
    done: int = 0
    total: int = 0
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class Downloads:
    def __init__(self, *, settings, client_factory: Callable[[], httpx.Client] | None = None, runner=subprocess.run):
        self.settings = settings
        self._client = client_factory or (lambda: httpx.Client(trust_env=False))
        self._run = runner
        self.jobs: dict[str, Job] = {name: Job() for name in ("voices", "llama-vulkan", "llama-cpu", "local-images")}

    # ---- what's there ---------------------------------------------------------------------------------------
    def installed(self, name: str) -> bool:
        if name == "voices":
            return all((tts_dir() / f["file"]).is_file() for f in KOKORO)
        if name.startswith("llama-"):
            return find_server(llama_dir(name.removeprefix("llama-"))) is not None
        if name == "local-images":
            return all(importlib.util.find_spec(m) is not None for m in ("torch", "diffusers", "transformers"))
        raise KeyError(name)

    def can_install_packages(self) -> bool:
        return bool(os.environ.get("AETHEL_UV")) and bool(os.environ.get("AETHEL_RESOURCES"))

    def status(self) -> dict:
        return {name: {"installed": self.installed(name), "running": job.running, "error": job.error,
                       "progress": (job.done / job.total) if job.total else 0.0}
                for name, job in self.jobs.items()} | {"packages_installable": self.can_install_packages()}

    # ---- starting one ---------------------------------------------------------------------------------------
    def start(self, name: str) -> bool:
        job = self.jobs[name]
        if name == "local-images" and not self.can_install_packages():
            raise ModelError("Local image support installs itself only in the installed app. In a dev setup run: "
                             "pip install -r backend/requirements/local-images.txt")
        with job.lock:
            if job.running:
                return False
            job.running, job.error, job.done, job.total = True, None, 0, 0
        threading.Thread(target=self._run_job, args=(name,), name=f"download-{name}", daemon=True).start()
        return True

    def _run_job(self, name: str) -> None:
        job = self.jobs[name]
        try:
            if name == "voices":
                self._voices(job)
            elif name.startswith("llama-"):
                self._llama(job, name.removeprefix("llama-"))
            else:
                self._packages(job)
        except Exception as exc:  # whatever it was, the section must hear it stopped
            log.exception("download %s failed", name)
            job.error = str(exc) if isinstance(exc, ModelError) else f"It didn't work ({type(exc).__name__}: {exc})."
        finally:
            job.running = False

    def _voices(self, job: Job) -> None:
        job.total = sum(f["size"] for f in KOKORO)
        base = 0
        with self._client() as client:
            for f in KOKORO:
                if not (tts_dir() / f["file"]).is_file():
                    download("kokoro", client, tts_dir(), spec={**f, "url": KOKORO_URL + f["file"]},
                             progress=lambda d, _t, b=base: setattr(job, "done", b + d))
                base += f["size"]
                job.done = base

    def _llama(self, job: Job, flavor: str) -> None:
        spec = LLAMA[flavor]
        job.total = spec["size"]
        cache = aethel_home() / "downloads"
        with self._client() as client:
            archive = download("llama", client, cache, spec={**spec, "url": LLAMA_URL + spec["file"]},
                               progress=lambda d, _t: setattr(job, "done", d))
        dest = llama_dir(flavor)
        safe_extract(archive, dest)
        archive.unlink(missing_ok=True)
        server = find_server(dest)
        if server is None:
            raise ModelError("The download didn't contain llama-server.exe.")
        local = self.settings.get().local_llm.model_dump()
        self.settings.update({"local_llm": {**local, "server_path": str(server)}})  # use it straight away

    def _packages(self, job: Job) -> None:
        uv, resources = os.environ["AETHEL_UV"], Path(os.environ["AETHEL_RESOURCES"])
        log_path = aethel_home() / "logs" / "local-images.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as out:
            result = self._run([uv, "pip", "install", "--python", sys.executable, "-r",
                                str(resources / "requirements" / "local-images.txt")],
                               stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0:
            raise ModelError(f"Installing local image support failed. The details are in {log_path}.")
