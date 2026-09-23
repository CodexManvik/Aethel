"""Owns the local llama-server subprocess (ported from v1 generation.py).

Chat history lives in SQLite, not in llama-server, so stopping/respawning the
server (e.g. to lend VRAM to image generation) never loses a conversation.
"""
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import httpx

from ..paths import MODELS_DIR, PROJECT_ROOT
from ..settings import LocalLLMSettings
from .catalog import LOCAL_BASE_URL

log = logging.getLogger("aethel.local_llama")
BINARY_NAME = "llama-server.exe" if os.name == "nt" else "llama-server"
STARTUP_TIMEOUT_S = int(os.environ.get("LLAMA_STARTUP_TIMEOUT", "120"))


class LocalLLMUnavailable(Exception):
    pass


class LocalLlama:
    def __init__(
        self,
        settings_provider: Callable[[], LocalLLMSettings],
        *,
        base_url: str = LOCAL_BASE_URL,
        models_dir: Path = MODELS_DIR,
    ):
        self._settings = settings_provider
        self.base_url = base_url.rstrip("/")
        self.models_dir = models_dir
        self.lifecycle_lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._suspended = False

    # ---- discovery -------------------------------------------------------
    def find_binary(self) -> Path | None:
        override = os.environ.get("LLAMA_SERVER_BINARY")
        if override:
            p = Path(override)
            if p.is_file():
                return p
            if (p / BINARY_NAME).is_file():
                return p / BINARY_NAME
        on_path = shutil.which(BINARY_NAME)
        if on_path:
            return Path(on_path)
        for candidate in (PROJECT_ROOT / "bin" / "llama" / BINARY_NAME, PROJECT_ROOT / "bin" / BINARY_NAME):
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _scan(directory: Path) -> tuple[Path | None, Path | None]:
        if not directory.is_dir():
            return None, None
        main, proj, main_size = None, None, -1
        for f in sorted(directory.glob("*.gguf")):
            if "mmproj" in f.name.lower():
                proj = f
                continue
            size = f.stat().st_size
            if size > main_size:
                main, main_size = f, size
        return main, proj

    def discover_model(self) -> tuple[Path | None, Path | None]:
        override = self._settings().model_path
        if override and Path(override).is_file():
            chosen = Path(override)
            proj = next((s for s in sorted(chosen.parent.glob("*.gguf")) if "mmproj" in s.name.lower()), None)
            return chosen, proj
        return self._scan(self.models_dir / "llm")

    # ---- process ----------------------------------------------------------
    def _port(self) -> int:
        match = re.search(r":(\d+)", self.base_url)
        return int(match.group(1)) if match else 8080

    def build_command(self, binary: Path, model: Path, mmproj: Path | None, port: int) -> list[str]:
        s = self._settings()
        cmd = [
            str(binary), "-m", str(model), "--port", str(port),
            "-c", str(s.context_size), "-t", str(s.threads),
            "--context-shift", "--cache-reuse", "256", "--log-disable",
        ]
        if s.gpu_layers > 0:
            cmd += ["-ngl", str(s.gpu_layers)]
        if mmproj is not None:
            cmd += ["--mmproj", str(mmproj)]
        return cmd

    def is_up(self) -> bool:
        try:
            return httpx.get(f"{self.base_url}/models", timeout=1.5).status_code == 200
        except httpx.HTTPError:
            return False

    def ensure_running(self) -> None:
        with self.lifecycle_lock:
            if self.is_up():
                return
            model, mmproj = self.discover_model()
            if model is None:
                raise LocalLLMUnavailable(
                    f"No local model found. Put a .gguf file in {self.models_dir / 'llm'} "
                    "or choose one in Settings → Local model."
                )
            binary = self.find_binary()
            if binary is None:
                raise LocalLLMUnavailable(
                    "llama-server not found. Install it (scripts/install.ps1) or set LLAMA_SERVER_BINARY."
                )
            port = self._port()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    raise LocalLLMUnavailable(f"Port {port} is busy but is not answering as llama-server.")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            log.info("starting llama-server: %s", model.name)
            self._process = subprocess.Popen(
                self.build_command(binary, model, mmproj, port),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
            )
            deadline = time.monotonic() + STARTUP_TIMEOUT_S
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    code = self._process.returncode
                    self._process = None
                    raise LocalLLMUnavailable(f"llama-server exited during startup (code {code}).")
                if self.is_up():
                    return
                time.sleep(1)
            self.stop()
            raise LocalLLMUnavailable(f"llama-server did not become ready within {STARTUP_TIMEOUT_S}s.")

    def stop(self) -> None:
        with self.lifecycle_lock:
            proc, self._process = self._process, None
            if proc is None:
                return
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def suspend(self) -> bool:
        with self.lifecycle_lock:
            if self._process is None:
                return False
            self.stop()
            self._suspended = True
            return True

    def resume(self) -> bool:
        with self.lifecycle_lock:
            if not self._suspended:
                return True
            try:
                self.ensure_running()
            except LocalLLMUnavailable as exc:
                log.error("resume failed: %s", exc)
                return False
            self._suspended = False
            return True
