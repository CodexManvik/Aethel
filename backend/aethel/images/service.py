"""ImageService: a persona's photo, from the request to the gallery (spec §10).

start() publishes ImagePending and returns at once; the picture is made in the background: references (anchor +
sheet + the passing pool, up to the backend's limit) → generate → identity check against the anchor → below τ_id,
one more try and the better of the two is kept → saved to personas/<id>/gallery/<image_id>.jpg with a .json sidecar
→ ImageReady (or ImageFailed). A face is checked only once the face models are installed; until then the score is
None ("not checked")."""
import asyncio
import io
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image

from ..personas.model import Persona
from ..protocol import ImageFailed, ImagePending, ImageReady
from .backend import ImageBackend, ImageError, ImageRequest, Size, pick_backend
from .compose import compose

log = logging.getLogger("aethel.images")
MAX_REFERENCES = 6
GALLERY = "gallery"
ANCHOR_CACHE = "anchor.npy"
Reason = Literal["chat", "task", "studio"]


@dataclass
class Job:
    image_id: str
    persona_id: str
    scene: str
    framing: str | None
    expression: str | None
    conversation_id: str | None
    message_id: str | None
    reason: Reason
    size: Size = "portrait"
    references: bool = True       # studio's first faces are drawn from the style alone
    message: str = ""             # why it failed


def to_jpeg(data: bytes) -> bytes:
    image = Image.open(io.BytesIO(data))
    buf = io.BytesIO()
    image.convert("RGB").save(buf, "JPEG", quality=92)
    return buf.getvalue()


def image_url(persona_id: str, image_id: str) -> str:
    return f"/api/personas/{persona_id}/files/{GALLERY}/{image_id}.jpg"


class ImageService:
    def __init__(self, *, personas, backends: dict[str, ImageBackend], settings, identity, hub, messages=None):
        self.personas, self.backends, self.settings = personas, backends, settings
        self.identity, self.hub, self.messages = identity, hub, messages
        self._tasks: set[asyncio.Task] = set()
        self._by_id: dict[str, asyncio.Task] = {}
        self._failed: dict[str, Job] = {}  # image id -> the job, for "Try again"

    # ---- which backend ---------------------------------------------------------------------------------------
    async def backend(self) -> ImageBackend | None:
        s = self.settings.get()
        return await pick_backend(s.images.backends, self.backends, s.private_mode)

    # ---- references and the anchor --------------------------------------------------------------------------
    def gallery(self, persona_id: str) -> list[dict]:
        """Newest first: {id, scene, backend, model, score, created, conversation_id, reference, url}."""
        folder = self.personas.folder(persona_id) / GALLERY
        out = []
        for side in folder.glob("*.json") if folder.is_dir() else []:
            try:
                meta = json.loads(side.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (folder / f"{side.stem}.jpg").is_file():
                out.append({**meta, "id": side.stem, "url": image_url(persona_id, side.stem)})
        return sorted(out, key=lambda m: m.get("created", ""), reverse=True)

    def set_reference(self, persona_id: str, image_id: str, on: bool) -> dict:
        """Adds a gallery photo to the reference pool or takes it out; the pool plus anchor and sheet stays ≤ 6."""
        persona = self.personas.get(persona_id)
        side = self.personas.folder(persona_id) / GALLERY / f"{image_id}.json"
        if persona is None or not side.is_file():
            raise KeyError(image_id)
        meta = json.loads(side.read_text(encoding="utf-8"))
        if on and not meta.get("reference"):
            if len(persona.anchors) + len(persona.sheet) + len(self._pool(persona_id)) >= MAX_REFERENCES:
                raise ValueError(f"A persona can have at most {MAX_REFERENCES} reference pictures.")
        meta["reference"] = on
        side.write_text(json.dumps(meta), encoding="utf-8")
        return {**meta, "id": image_id, "url": image_url(persona_id, image_id)}

    def _pool(self, persona_id: str) -> list[str]:
        return [f"{GALLERY}/{m['id']}.jpg" for m in self.gallery(persona_id) if m.get("reference")]

    def references(self, persona: Persona, limit: int) -> list[bytes]:
        folder = self.personas.folder(persona.id)
        out = []
        for rel in [*persona.anchors, *persona.sheet, *self._pool(persona.id)][:min(limit, MAX_REFERENCES)]:
            try:
                out.append((folder / rel).read_bytes())
            except OSError:
                log.warning("persona %s: reference %s is missing", persona.id, rel)
        return out

    def anchor_embedding(self, persona: Persona) -> np.ndarray | None:
        """Cached in personas/<id>/anchor.npy, keyed by the anchor's path and modification time."""
        if not persona.anchors or not self.identity.ready()[0]:
            return None
        folder = self.personas.folder(persona.id)
        anchor = folder / persona.anchors[0]
        if not anchor.is_file():
            return None
        key = f"{persona.anchors[0]}|{anchor.stat().st_mtime_ns}"
        cache, cache_key = folder / ANCHOR_CACHE, folder / (ANCHOR_CACHE + ".key")
        if cache.is_file() and cache_key.is_file() and cache_key.read_text(encoding="utf-8") == key:
            return np.load(cache)
        vec = self.identity.embedding_of(anchor.read_bytes())
        if vec is not None:
            np.save(cache, vec)
            cache_key.write_text(key, encoding="utf-8")
        return vec

    # ---- making one ----------------------------------------------------------------------------------------
    async def start(self, *, persona: Persona, scene: str, framing: str | None = None, expression: str | None = None,
                    conversation_id: str | None = None, message_id: str | None = None, reason: Reason = "chat",
                    size: Size = "portrait", references: bool = True) -> str:
        job = Job(uuid.uuid4().hex[:16], persona.id, scene, framing, expression, conversation_id, message_id, reason,
                  size, references)
        await self._pending(job)
        self._spawn(job)
        return job.image_id

    async def generate(self, **kw) -> dict:
        """Waits for the picture: {id, url, score, ok, message}. For the studio and tasks."""
        image_id = await self.start(**kw)
        task = self._by_id.get(image_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        return self.result(kw["persona"].id, image_id)

    def result(self, persona_id: str, image_id: str) -> dict:
        job = self._failed.get(image_id)
        if job is not None:
            return {"id": image_id, "ok": False, "message": job.message or "The photo didn't come out."}
        side = self.personas.folder(persona_id) / GALLERY / f"{image_id}.json"
        meta = json.loads(side.read_text(encoding="utf-8")) if side.is_file() else {}
        return {"id": image_id, "ok": bool(meta), "url": image_url(persona_id, image_id), "score": meta.get("score")}

    async def retry(self, image_id: str) -> str:
        job = self._failed.pop(image_id, None)
        if job is None:
            raise KeyError(image_id)
        await self._pending(job)
        self._spawn(job)
        return image_id

    def _spawn(self, job: Job) -> None:
        task = asyncio.ensure_future(self._run(job))
        self._tasks.add(task)
        self._by_id[job.image_id] = task

        def done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            if self._by_id.get(job.image_id) is t:
                del self._by_id[job.image_id]
        task.add_done_callback(done)

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _pending(self, job: Job) -> None:
        self._mark(job, "pending")
        await self.hub.publish(ImagePending(image_id=job.image_id, persona_id=job.persona_id,
                                            conversation_id=job.conversation_id, message_id=job.message_id,
                                            caption=" ".join(job.scene.split())[:80]))

    async def _run(self, job: Job) -> None:
        try:
            meta = await self._make(job)
        except ImageError as exc:
            await self._fail(job, str(exc), exc.retryable)
        except Exception as exc:
            log.exception("making a photo failed")
            await self._fail(job, f"The photo didn't come out ({type(exc).__name__}).", True)
        else:
            self._mark(job, "ready", meta.get("score"))
            await self.hub.publish(ImageReady(image_id=job.image_id, persona_id=job.persona_id,
                                              conversation_id=job.conversation_id, message_id=job.message_id,
                                              url=image_url(job.persona_id, job.image_id),
                                              identity_score=meta.get("score")))

    async def _fail(self, job: Job, message: str, retryable: bool) -> None:
        job.message = message
        self._failed[job.image_id] = job
        self._mark(job, "failed")
        await self.hub.publish(ImageFailed(image_id=job.image_id, persona_id=job.persona_id,
                                           conversation_id=job.conversation_id, message_id=job.message_id,
                                           message=message, retryable=retryable))

    async def _make(self, job: Job) -> dict:
        persona = self.personas.get(job.persona_id)
        if persona is None:
            raise ImageError("That persona no longer exists.")
        backend = await self.backend()
        if backend is None:
            raise ImageError("No image model is available.", retryable=True)
        refs = self.references(persona, backend.max_references) if job.references else []
        req = ImageRequest(compose(persona, job.scene, job.framing, job.expression, references=bool(refs)), refs,
                           job.size)
        anchor = await asyncio.to_thread(self.anchor_embedding, persona) if job.references else None
        threshold = self.settings.get().images.identity_threshold
        best_data, best_result, best_score, tries = None, None, None, 0
        for _ in range(2):
            result = await backend.generate(req)
            tries += 1
            data = await asyncio.to_thread(to_jpeg, result.data)
            score = (await asyncio.to_thread(self.identity.compare, data, anchor)) if anchor is not None else None
            if best_data is None or (score is not None and (best_score is None or score > best_score)):
                best_data, best_result, best_score = data, result, score
            if score is None or score >= threshold:
                break  # no face to check (scenery), unchecked, or it passed
        folder = self.personas.folder(persona.id) / GALLERY
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{job.image_id}.jpg").write_bytes(best_data)
        pool_room = MAX_REFERENCES - len(persona.anchors) - len(persona.sheet) - len(self._pool(persona.id))
        meta = {"scene": job.scene, "framing": job.framing, "expression": job.expression,
                "backend": best_result.backend, "model": best_result.model, "latency_ms": best_result.latency_ms,
                "score": None if best_score is None else round(best_score, 4),
                "low": best_score is not None and best_score < threshold, "tries": tries,
                "created": datetime.now(timezone.utc).isoformat(), "conversation_id": job.conversation_id,
                "reason": job.reason,
                "reference": job.reason == "chat" and best_score is not None and best_score >= threshold and pool_room > 0}
        (folder / f"{job.image_id}.json").write_text(json.dumps(meta), encoding="utf-8")
        return meta

    def _mark(self, job: Job, status: str, score: float | None = None) -> None:
        """The reply's meta.images, so a reload shows the photo (or that it failed)."""
        if self.messages is None or job.message_id is None:
            return
        entry = {"id": job.image_id, "persona_id": job.persona_id, "status": status, "score": score,
                 "caption": " ".join(job.scene.split())[:80]}

        def patch(meta: dict) -> dict:
            images = list(meta.get("images", []))
            at = next((i for i, e in enumerate(images) if e.get("id") == job.image_id), None)
            if at is None:
                images.append(entry)
            else:
                images[at] = entry
            return {**meta, "images": images}
        self.messages.merge_meta(job.message_id, patch)
