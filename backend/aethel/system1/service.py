"""System 1: fast typed judgments (spec §5), answered locally by Laya.

Never on the critical path: until the model is downloaded and loaded, or if
it fails, or System 1 is turned off, every call returns None and callers keep
their Phase 1 behaviour. Every answered call is logged for evaluation."""
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

import anyio

from ..paths import aethel_home
from ..settings import SettingsService
from ..store.db import Database
from ..store.repos import new_id, now_iso
from .laya import LayaModel

log = logging.getLogger("aethel.system1")
REPO = "receptron/laya-onnx"
REVISION = "68f27dfe5a27a54fb2b1fefc432f43f972e90868"
BUNDLE = ("laya.onnx", "laya.onnx.data", "laya_config.json", "tokenizer/tokenizer.json")


def default_model_dir() -> Path:
    return Path(os.environ.get("AETHEL_LAYA_DIR") or aethel_home() / "models" / "laya")


def _download(target: Path) -> None:
    from huggingface_hub import snapshot_download
    snapshot_download(REPO, revision=REVISION, local_dir=str(target))


class System1:
    def __init__(self, db: Database, settings: SettingsService, model_dir: Path | None = None,
                 loader: Callable[[Path], object] = LayaModel.load, download: Callable[[Path], None] = _download):
        self.db = db
        self.settings = settings
        self.model_dir = model_dir or default_model_dir()
        self._loader = loader
        self._download = download
        self._model = None
        self._status = "not loaded"
        self._starting: asyncio.Task | None = None

    def status(self) -> str:
        return self._status if self.settings.get().system1.enabled else "off"

    def start(self) -> None:
        """Download (first run, ~1.7 GB) and load in the background."""
        if self._starting is None and self.settings.get().system1.enabled:
            self._starting = asyncio.get_running_loop().create_task(self._prepare())

    async def _prepare(self) -> None:
        try:
            if not all((self.model_dir / f).is_file() for f in BUNDLE):
                self._status = "downloading"
                await anyio.to_thread.run_sync(self._download, self.model_dir)
            self._status = "loading"
            self._model = await anyio.to_thread.run_sync(self._loader, self.model_dir)
            self._status = "ready"
        except Exception as exc:
            log.warning("System 1 unavailable: %r", exc)
            self._status = f"failed: {exc}"[:200]

    async def ask(self, state, questions: dict, purpose: str) -> dict | None:
        """Jev-shaped answers keyed by question id, or None when System 1 can't answer."""
        if self._model is None or not self.settings.get().system1.enabled:
            return None
        t0 = time.perf_counter()
        answers, error = None, None
        try:
            answers = (await anyio.to_thread.run_sync(self._model.system_one, state, questions))["answers"]
        except Exception as exc:  # a bad question or a runtime failure: fall back, never crash the caller
            error = repr(exc)[:500]
            log.warning("System 1 call (%s) failed: %s", purpose, error)
        self.db.execute(
            "INSERT INTO s1_calls (id, purpose, state, questions, answers, error, latency_ms, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id("s1"), purpose, json.dumps(state, ensure_ascii=False), json.dumps(questions, ensure_ascii=False),
             json.dumps(answers) if answers is not None else None, error,
             int((time.perf_counter() - t0) * 1000), now_iso()))
        return answers

    async def choice(self, state, instructions: str, options: dict[str, str | None],
                     purpose: str) -> tuple[str, dict[str, float], float] | None:
        """(choice, probabilities, confidence), or None."""
        answers = await self.ask(state, {"q": {"type": "choice", "instructions": instructions, "criteria": options}},
                                 purpose)
        if answers is None:
            return None
        a = answers["q"]
        return a["choice"], a["probabilities"], a["confidence"]

    async def noul(self, state, instructions: str, purpose: str) -> float | None:
        """P(yes), or None."""
        answers = await self.ask(state, {"q": {"type": "noul", "instructions": instructions}}, purpose)
        return None if answers is None else answers["q"]["noul"]
