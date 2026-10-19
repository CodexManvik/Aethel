"""PersonaStore: one folder per persona under ~/.aethel/personas/, persona.md the source of truth."""
import logging
import os
import re
import shutil
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..chat.persona import AETHEL_PERSONA
from .model import ID_RE, Persona, PersonaVoice, parse_persona, render_persona

log = logging.getLogger("aethel.personas")
BUILTIN_ID = "aethel"
FILE = "persona.md"
STYLE_FILE = "style.md"
TRASH = ".trash"
RETRIES, RETRY_S = 5, 0.02
FIXED_FOR_BUILTIN = {"id", "name", "built_in"}


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return s or "persona"


def builtin_persona() -> Persona:
    """Aethel itself: no portrait (it shows its monogram) and no tone engine (spec §9)."""
    return Persona(id=BUILTIN_ID, name="Aethel", built_in=True, tone_engine=False, image_frequency="never",
                   sections={"Personality": AETHEL_PERSONA.strip()})


class PersonaStore:
    def __init__(self, root: Path, known_voices=None, in_use=None):
        self.root = root
        self.known_voices = known_voices  # a callable giving the installed voices, or None (names only checked)
        self.in_use = in_use              # a callable: does any conversation use this persona id?
        self._lock = threading.Lock()     # create/update/delete come from request threads

    def _known(self):
        try:
            return self.known_voices() if self.known_voices is not None else None
        except Exception:  # the voice models aren't there: check the names' shape only
            return None

    def folder(self, pid: str) -> Path:
        if not re.fullmatch(ID_RE, pid):
            raise ValueError(f"not a persona id: {pid!r}")
        return self.root / pid

    def _read(self, pid: str) -> Persona | None:
        path = self.folder(pid) / FILE
        for attempt in range(RETRIES):
            try:
                return parse_persona(path.read_text(encoding="utf-8"), pid)
            except FileNotFoundError:
                return None
            except PermissionError:  # Windows: a save's os.replace for a moment; reading again succeeds
                if attempt == RETRIES - 1:
                    raise
                time.sleep(RETRY_S)
            except (ValueError, UnicodeDecodeError) as exc:  # a hand-edited file that no longer parses: skip it
                log.warning("persona %s can't be read: %s", pid, exc)
                return None
        return None

    def _write(self, p: Persona) -> None:
        folder = self.folder(p.id)
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f".{FILE}.tmp"
        tmp.write_text(render_persona(p), encoding="utf-8")
        for attempt in range(RETRIES):
            try:
                os.replace(tmp, folder / FILE)
                return
            except PermissionError:  # a reader has the file open (Windows)
                if attempt == RETRIES - 1:
                    raise
                time.sleep(RETRY_S)

    def _taken(self, pid: str) -> bool:
        """An id is never handed out twice: not while its folder exists, not while it's in the trash, and not while
        a conversation (and the memories between them) still carries it."""
        if (self.root / pid).exists() or (self.root / TRASH).is_dir() and any(
                re.fullmatch(rf"{re.escape(pid)}-\d{{8}}-\d{{6}}-\d+", t.name) for t in (self.root / TRASH).iterdir()):
            return True
        return self.in_use is not None and self.in_use(pid)

    def list(self) -> list[Persona]:
        if not self.root.is_dir():
            return []
        found = [p for d in self.root.iterdir() if d.is_dir() and re.fullmatch(ID_RE, d.name)
                 and (p := self._read(d.name)) is not None]
        return sorted(found, key=lambda p: (not p.built_in, p.name.lower(), p.id))

    def get(self, pid: str) -> Persona | None:
        try:
            return self._read(pid)
        except ValueError:
            return None

    def builtin(self) -> Persona:
        return self.get(BUILTIN_ID) or builtin_persona()

    def create(self, p: Persona) -> Persona:
        """A new folder; the id comes from the name (slug, then -2, -3 … on a clash), never from the caller."""
        p.voice.to_blend(self._known())
        with self._lock:
            base, n = slug(p.name), 1
            pid = base
            while self._taken(pid):
                n += 1
                pid = f"{base[:37]}-{n}"
            made = p.model_copy(update={"id": pid, "built_in": False,
                                        "created": p.created or date.today().isoformat()})
            self._write(made)
        return made

    def update(self, pid: str, patch: dict[str, Any]) -> Persona:
        """Frontmatter fields and sections (a partial dict: only the sections named change). The built-in's id and
        name stay as they are. Raises KeyError if there's no such persona, ValueError if the result isn't valid."""
        with self._lock:
            current = self.get(pid)
            if current is None:
                raise KeyError(pid)
            patch = dict(patch)
            patch.pop("id", None)
            patch.pop("built_in", None)
            if current.built_in:
                for key in FIXED_FOR_BUILTIN:
                    patch.pop(key, None)
            data = current.model_dump()
            if "sections" in patch:
                data["sections"] = {**data["sections"], **(patch.pop("sections") or {})}
            data.update(patch)
            updated = Persona(**data)
            updated.voice.to_blend(self._known())
            self._write(updated)
        return updated

    def delete(self, pid: str) -> bool:
        """Moves the folder to personas/.trash/ (never the built-in); False if there's nothing to delete."""
        with self._lock:
            p = self.get(pid)
            if p is None or p.built_in:
                return False
            trash = self.root / TRASH
            trash.mkdir(parents=True, exist_ok=True)
            shutil.move(str(self.folder(pid)), str(trash / f"{pid}-{datetime.now():%Y%m%d-%H%M%S-%f}"))
        return True

    def style_directive(self, pid: str) -> str | None:
        try:
            text = (self.folder(pid) / STYLE_FILE).read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            return None
        return text or None

    def set_style_directive(self, pid: str, text: str) -> None:
        path = self.folder(pid) / STYLE_FILE
        if text.strip():
            path.write_text(text.strip() + "\n", encoding="utf-8")
        else:
            path.unlink(missing_ok=True)

    def ensure_builtin(self) -> None:
        """Creates personas/aethel/ once; an edited built-in is left as the user made it."""
        with self._lock:
            if not (self.folder(BUILTIN_ID) / FILE).exists():
                self._write(builtin_persona().model_copy(update={"created": date.today().isoformat()}))

    def migrate_legacy(self, legacy: Persona | None, sentinel: Path) -> str | None:
        """The old app's companion becomes a persona folder, once (spec §9, D6). `legacy` is None when there's no
        old data, and then nothing happens, not even the sentinel, so data that appears later still migrates."""
        if legacy is None or sentinel.exists():
            return None
        try:
            made = self.create(legacy)
        except ValueError:  # its old voice isn't installed here: keep the persona, with the default voice
            made = self.create(legacy.model_copy(update={"voice": PersonaVoice()}))
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text(f"{made.id}\n", encoding="utf-8")
        log.info("migrated the legacy companion to persona %s", made.id)
        return made.id
