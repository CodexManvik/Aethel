"""A persona's imported lines: real messages from a chat export, recalled as style examples (never as facts or
instructions). Same shape as the episodic index: SQLite holds each line and its vector, a per-persona turbovec file
shortlists, exact cosine ranks; a missing or out-of-step file is rebuilt from the rows."""
import logging
import os
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from ..store.db import Database
from ..store.repos import now_iso
from .distill import Line

log = logging.getLogger("aethel.personas")
BIT_WIDTH = 4
MAX_LINES = 2000    # the most recent; embedding more costs minutes on a CPU for little gain in style
MIN_CHARS = 8       # "ok", "lol": no style to show
BATCH = 64
INDEX_FILE = "lines.tvim"


class PersonaLines:
    def __init__(self, db: Database, embed: Callable[[list[str]], np.ndarray], folder: Callable[[str], Path]):
        self.db, self.embed, self.folder = db, embed, folder
        self._lock = threading.RLock()
        self._indexes: dict[str, object] = {}

    def _path(self, pid: str) -> Path:
        return self.folder(pid) / INDEX_FILE

    def _write(self, pid: str, index) -> None:
        path = self._path(pid)
        if len(index) == 0:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp.tvim")
        index.write(str(tmp))
        os.replace(tmp, path)

    def _load(self, pid: str):
        if pid in self._indexes:
            return self._indexes[pid]
        from turbovec import IdMapIndex
        count, top = self.db.query_one("SELECT count(*), max(id) FROM persona_lines WHERE persona_id = ?", (pid,))
        index = None
        path = self._path(pid)
        if path.exists():
            try:
                index = IdMapIndex.load(str(path))
                if len(index) != count or (top is not None and not index.contains(top)):
                    raise ValueError(f"has {len(index)} entries, the database {count}")
            except Exception as exc:
                log.warning("persona %s: lines index unusable (%r); rebuilding it", pid, exc)
                index = None
        if index is None:
            index = IdMapIndex(bit_width=BIT_WIDTH)
            rows = self.db.query("SELECT id, vector FROM persona_lines WHERE persona_id = ? ORDER BY id", (pid,))
            if rows:
                index.add_with_ids(np.stack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows]),
                                   np.array([r["id"] for r in rows], dtype=np.uint64))
            self._write(pid, index)
        self._indexes[pid] = index
        return index

    def count(self, pid: str) -> int:
        return self.db.query_one("SELECT count(*) FROM persona_lines WHERE persona_id = ?", (pid,))[0]

    def replace(self, pid: str, lines: list[Line]) -> int:
        """The persona's corpus becomes these lines (the latest MAX_LINES worth keeping). Blocking: embeds."""
        seen, keep = set(), []
        for l in reversed(lines):
            text = l.text.strip()
            if len(text) >= MIN_CHARS and text not in seen:
                seen.add(text)
                keep.append(Line(l.speaker, text, l.ts))
            if len(keep) >= MAX_LINES:
                break
        keep.reverse()
        vectors = [np.asarray(self.embed([l.text for l in keep[i:i + BATCH]]), dtype=np.float32)
                   for i in range(0, len(keep), BATCH)]
        from turbovec import IdMapIndex
        with self._lock:
            index = IdMapIndex(bit_width=BIT_WIDTH)
            with self.db.transaction():
                self.db.execute("DELETE FROM persona_lines WHERE persona_id = ?", (pid,))
                ids, ts = [], now_iso()
                for l, vec in zip(keep, (v for batch in vectors for v in batch)):
                    cur = self.db.execute("INSERT INTO persona_lines (persona_id, text, ts, vector, created_at)"
                                          " VALUES (?, ?, ?, ?, ?)", (pid, l.text, l.ts, vec.tobytes(), ts))
                    ids.append(cur.lastrowid)
                if ids:
                    index.add_with_ids(np.concatenate(vectors), np.array(ids, dtype=np.uint64))
            self._indexes[pid] = index
            self._write(pid, index)
        return len(keep)

    def remove(self, pid: str) -> None:
        with self._lock:
            self.db.execute("DELETE FROM persona_lines WHERE persona_id = ?", (pid,))
            self._indexes.pop(pid, None)

    def search(self, pid: str, query: str, *, k: int, min_score: float,
               vector: np.ndarray | None = None) -> list[tuple[str, float]]:
        if k <= 0:
            return []
        with self._lock:
            index = self._load(pid)
            if len(index) == 0:
                return []
            q = np.asarray(self.embed([query])[0] if vector is None else vector, dtype=np.float32)
            _, found = index.search(q[None, :], k=min(len(index), k * 8))
            shortlist = [int(i) for i in found[0]]
        if not shortlist:
            return []
        rows = self.db.query(f"SELECT id, text, vector FROM persona_lines WHERE id IN ({','.join('?' * len(shortlist))})",
                             tuple(shortlist))
        scored = [(r["text"], float(np.dot(np.frombuffer(r["vector"], dtype=np.float32), q))) for r in rows]
        scored = [s for s in scored if s[1] >= min_score]
        scored.sort(key=lambda s: -s[1])
        return scored[:k]
