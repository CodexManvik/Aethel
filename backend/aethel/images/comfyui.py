"""ComfyUI with the user's own workflow (exported with "Save (API format)"). The workflow marks what Aethel fills in
with placeholders in its string inputs:

  {{prompt}}     the composed prompt (may sit inside a longer string)
  {{negative}}   a short negative prompt
  {{seed}}       a random seed (the whole value: becomes a number)
  {{width}}      {{height}}   the picture's size (whole values: become numbers)
  {{reference}}  the anchor portrait, uploaded first (a LoadImage node's "image" input)

Flow: upload the reference (/upload/image) → POST /prompt → poll /history/<id> → fetch the first output image
(/view). A workflow without {{reference}} is text only (faces may vary)."""
import json
import random
import re
import time
import uuid
from pathlib import Path
from typing import Callable

import anyio
import httpx

from .backend import ImageError, ImageRequest, ImageResult, is_loopback

PLACEHOLDERS = ("prompt", "negative", "seed", "width", "height", "reference")
NUMERIC = {"seed", "width", "height"}
SIDES = {"portrait": (832, 1216), "square": (1024, 1024), "landscape": (1216, 832)}
NEGATIVE = "blurry, deformed, extra fingers, text, watermark, logo"
POLL_S, TIMEOUT_S = 1.0, 300.0
_TOKEN = re.compile(r"\{\{(\w+)\}\}")


class WorkflowError(ValueError):
    pass


def load_workflow(path: str) -> dict:
    """The API-format workflow: {node_id: {class_type, inputs}}. Raises WorkflowError saying what's wrong."""
    if not path:
        raise WorkflowError("No workflow file is chosen.")
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise WorkflowError(f"The workflow file can't be read: {exc.strerror or exc}") from exc
    except ValueError as exc:
        raise WorkflowError("The workflow file isn't JSON.") from exc
    if not isinstance(data, dict) or not data or not all(isinstance(n, dict) and "class_type" in n for n in data.values()):
        raise WorkflowError("That isn't an API-format workflow: in ComfyUI use Workflow → Export (API).")
    if "{{prompt}}" not in json.dumps(data):
        raise WorkflowError("The workflow has no {{prompt}} placeholder: put it in the positive prompt's text.")
    return data


def placeholders(workflow: dict) -> list[str]:
    found = set(_TOKEN.findall(json.dumps(workflow)))
    return [p for p in PLACEHOLDERS if p in found]


def fill(workflow: dict, values: dict) -> dict:
    """A copy with every placeholder replaced; a string that is exactly a numeric placeholder becomes a number."""
    def walk(v):
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, str):
            whole = _TOKEN.fullmatch(v)
            if whole and whole.group(1) in NUMERIC:
                return int(values[whole.group(1)])
            return _TOKEN.sub(lambda m: str(values.get(m.group(1), m.group(0))), v)
        return v
    return walk(workflow)


class ComfyUI:
    def __init__(self, *, base_url: Callable[[], str], workflow_path: Callable[[], str], http: httpx.AsyncClient,
                 sleep=anyio.sleep, clock=time.monotonic):
        self._base, self._path, self.http, self._sleep, self._clock = base_url, workflow_path, http, sleep, clock

    @property
    def name(self) -> str:
        return f"comfyui:{Path(self._path()).stem or 'workflow'}"

    @property
    def cloud(self) -> bool:
        return not is_loopback(self._base())

    @property
    def max_references(self) -> int:
        try:
            return 1 if "reference" in placeholders(load_workflow(self._path())) else 0
        except WorkflowError:
            return 0

    async def available(self) -> tuple[bool, str]:
        if not self._base():
            return False, "Set the ComfyUI server's URL in Settings → Images."
        try:
            load_workflow(self._path())
        except WorkflowError as exc:
            return False, str(exc)
        return True, ""

    async def generate(self, req: ImageRequest) -> ImageResult:
        base = self._base().rstrip("/")
        try:
            workflow = load_workflow(self._path())
        except WorkflowError as exc:
            raise ImageError(str(exc)) from exc
        started = time.monotonic()
        width, height = SIDES[req.size]
        values = {"prompt": req.prompt, "negative": NEGATIVE, "seed": random.randrange(2**31), "width": width,
                  "height": height}
        try:
            if "reference" in placeholders(workflow):
                if not req.references:
                    raise ImageError("This workflow needs a reference picture, and the persona has no face yet.")
                values["reference"] = await self._upload(base, req.references[0])
            r = await self.http.post(f"{base}/prompt", json={"prompt": fill(workflow, values),
                                                             "client_id": uuid.uuid4().hex}, timeout=30)
            if r.status_code >= 400:
                raise ImageError(f"ComfyUI refused the workflow ({r.status_code}): {r.text[:200]}")
            prompt_id = r.json()["prompt_id"]
            image = await self._wait(base, prompt_id)
            got = await self.http.get(f"{base}/view", params=image, timeout=60)
            got.raise_for_status()
        except httpx.HTTPError as exc:
            raise ImageError(f"Couldn't reach ComfyUI: {type(exc).__name__}", retryable=True) from exc
        return ImageResult(data=got.content, mime=got.headers.get("content-type", "image/png").split(";")[0],
                           backend="comfyui", model=Path(self._path()).stem,
                           latency_ms=int((time.monotonic() - started) * 1000))

    async def _upload(self, base: str, data: bytes) -> str:
        r = await self.http.post(f"{base}/upload/image", files={"image": (f"aethel-{uuid.uuid4().hex[:8]}.jpg", data,
                                                                          "image/jpeg")},
                                 data={"overwrite": "true"}, timeout=60)
        r.raise_for_status()
        body = r.json()
        return f"{body['subfolder']}/{body['name']}" if body.get("subfolder") else body["name"]

    async def _wait(self, base: str, prompt_id: str) -> dict:
        deadline = self._clock() + TIMEOUT_S
        while self._clock() < deadline:
            r = await self.http.get(f"{base}/history/{prompt_id}", timeout=30)
            r.raise_for_status()
            entry = r.json().get(prompt_id)
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    raise ImageError("ComfyUI's workflow failed while running.")
                for output in (entry.get("outputs") or {}).values():
                    for image in output.get("images") or []:
                        return {"filename": image["filename"], "subfolder": image.get("subfolder", ""),
                                "type": image.get("type", "output")}
                if status.get("completed"):
                    raise ImageError("ComfyUI finished without an image: does the workflow end in Save Image?")
            await self._sleep(POLL_S)
        raise ImageError("ComfyUI took too long.", retryable=True)
