"""Episodic memory (Phase 3 spec §3): Aethel's own chat history, one entry per exchange (a user message and
the reply to it), for "remember when…" recall across conversations.

SQLite holds each exchange's text and vector and is the source of truth. The turbovec index (4-bit
quantised) is a cache that shortlists candidates; the final ranking is exact cosine against the stored
vectors. A missing, unreadable or out-of-step index file is rebuilt from the rows without re-embedding.

turbovec's IdMapIndex (probed 2026-09-30): search on an empty index returns empty arrays, and k larger
than the index returns everything; add_with_ids raises on a duplicate id; files are .tvim."""
import json
import logging
import math
import os
import threading
from pathlib import Path
from typing import Callable

import numpy as np
from pydantic import BaseModel

from ..store.db import Database

log = logging.getLogger("aethel.memory")
BIT_WIDTH = 4
SIDE_CHARS = 1000   # per side of an exchange; bge truncates at 512 tokens anyway
TIE_BUCKET = 0.02   # scores this close count as a tie, broken by recency
BATCH = 32


class Episode(BaseModel):
    id: int
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    text: str
    created_at: str


def exchange_text(user: str, reply: str) -> str:
    return f"User: {user.strip()[:SIDE_CHARS]}\nAethel: {reply.strip()[:SIDE_CHARS]}"


def _episode(row) -> Episode:
    return Episode(**{k: row[k] for k in Episode.model_fields})


class EpisodicIndex:
    def __init__(self, db: Database, embed: Callable[[list[str]], np.ndarray], path: Path):
        self.db = db
        self.embed = embed
        self.path = path
        self._lock = threading.RLock()
        self._index = None
        self._rebuilding = False

    # ---- the index file ------------------------------------------------------------
    def _write(self, index) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if len(index) == 0:
            self.path.unlink(missing_ok=True)
            return
        tmp = self.path.with_suffix(".tmp.tvim")
        index.write(str(tmp))
        os.replace(tmp, self.path)

    def _from_rows(self):
        from turbovec import IdMapIndex
        index = IdMapIndex(bit_width=BIT_WIDTH)
        rows = self.db.query("SELECT id, vector FROM episodes ORDER BY id")
        if rows:
            index.add_with_ids(np.stack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows]),
                               np.array([r["id"] for r in rows], dtype=np.uint64))
        self._write(index)
        return index

    def _load(self):
        if self._index is not None:
            return self._index
        from turbovec import IdMapIndex
        count, top = self.db.query_one("SELECT count(*), max(id) FROM episodes")
        index = None
        if self.path.exists():
            try:
                index = IdMapIndex.load(str(self.path))
                if len(index) != count or (top is not None and not index.contains(top)):
                    raise ValueError(f"has {len(index)} entries, the database {count}")
            except Exception as exc:
                log.warning("episodic index unusable (%r); rebuilding it from the database", exc)
                index = None
        elif count:
            log.info("episodic index missing; rebuilding it from the database")
        self._index = index if index is not None else self._from_rows()
        return self._index

    # ---- writing -------------------------------------------------------------------
    def _indexed(self, assistant_ids: list[str]) -> set[str]:
        if not assistant_ids:
            return set()
        rows = self.db.query(f"SELECT assistant_message_id FROM episodes WHERE assistant_message_id IN "
                             f"({','.join('?' * len(assistant_ids))})", tuple(assistant_ids))
        return {r[0] for r in rows}

    def _add_many(self, pairs: list[tuple]) -> int:
        """pairs: (conversation_id, user_id, user_text, assistant_id, reply_text, created_at)."""
        pairs = [p for p in pairs if p[2].strip() and p[4].strip()]
        if not pairs:
            return 0
        texts = [exchange_text(p[2], p[4]) for p in pairs]
        vectors = np.asarray(self.embed(texts), dtype=np.float32)  # outside the lock: the slow part
        added = 0
        with self._lock:
            index = self._load()
            fresh = [(p, t, v) for p, t, v in zip(pairs, texts, vectors) if p[3] not in self._indexed([p[3]])]
            if not fresh:
                return 0
            with self.db.transaction():
                ids = []
                for (conv, uid, _, aid, _, ts), text, vec in fresh:
                    cur = self.db.execute(
                        "INSERT INTO episodes (conversation_id, user_message_id, assistant_message_id, text, vector,"
                        " created_at) VALUES (?, ?, ?, ?, ?, ?)", (conv, uid, aid, text, vec.tobytes(), ts))
                    ids.append(cur.lastrowid)
                    added += 1
                index.add_with_ids(np.stack([v for _, _, v in fresh]), np.array(ids, dtype=np.uint64))
            self._write(index)
        return added

    def add_exchange(self, user_msg, assistant_msg) -> Episode | None:
        """Index one finished exchange. None when it's empty or already indexed."""
        added = self._add_many([(assistant_msg.conversation_id, user_msg.id, user_msg.content, assistant_msg.id,
                                 assistant_msg.content, assistant_msg.created_at)])
        if not added:
            return None
        return _episode(self.db.query_one("SELECT * FROM episodes WHERE assistant_message_id = ?",
                                          (assistant_msg.id,)))

    def _exchanges(self) -> list[tuple]:
        """Every finished chat exchange: a user message directly followed by a complete, non-empty reply,
        neither of them part of a task."""
        rows = self.db.query("SELECT id, conversation_id, role, content, status, meta, created_at FROM messages"
                             " ORDER BY conversation_id, rowid")
        out = []
        for u, a in zip(rows, rows[1:]):
            if (u["conversation_id"] == a["conversation_id"] and u["role"] == "user" and a["role"] == "assistant"
                    and a["status"] == "complete" and a["content"].strip() and u["content"].strip()
                    and not json.loads(u["meta"]).get("task_id") and not json.loads(a["meta"]).get("task_id")):
                out.append((u["conversation_id"], u["id"], u["content"], a["id"], a["content"], a["created_at"]))
        return out

    def catch_up(self, on_progress: Callable[[int, int], None] | None = None) -> int:
        """Index the exchanges that have no episode yet (missed while the app was closed, or a rebuild)."""
        done_ids = {r[0] for r in self.db.query("SELECT assistant_message_id FROM episodes")}
        todo = [p for p in self._exchanges() if p[3] not in done_ids]
        added = 0
        for start in range(0, len(todo), BATCH):
            added += self._add_many(todo[start:start + BATCH])
            if on_progress is not None:
                on_progress(min(start + BATCH, len(todo)), len(todo))
        return added

    def remove_conversation(self, conversation_id: str) -> int:
        with self._lock:
            ids = [r[0] for r in self.db.query("SELECT id FROM episodes WHERE conversation_id = ?", (conversation_id,))]
            if not ids:
                return 0
            index = self._load()
            for i in ids:
                index.remove(i)
            self.db.execute("DELETE FROM episodes WHERE conversation_id = ?", (conversation_id,))
            self._write(index)
        return len(ids)

    def rebuild(self, on_progress: Callable[[int, int], None] | None = None) -> int:
        """Re-embed everything (e.g. after changing the embedder). Searches keep working meanwhile,
        over whatever has been re-indexed so far."""
        from turbovec import IdMapIndex
        with self._lock:
            if self._rebuilding:
                return 0
            self._rebuilding = True
            self.db.execute("DELETE FROM episodes")
            self._index = IdMapIndex(bit_width=BIT_WIDTH)
            self._write(self._index)
        try:
            return self.catch_up(on_progress)
        finally:
            self._rebuilding = False

    # ---- reading -------------------------------------------------------------------
    def search(self, query: str, *, k: int, min_score: float,
               exclude_messages: set[str] | frozenset = frozenset()) -> list[tuple[Episode, float]]:
        if k <= 0:
            return []
        q = np.asarray(self.embed([query])[0], dtype=np.float32)
        with self._lock:
            index = self._load()
            if len(index) == 0:
                return []
            _, found = index.search(q[None, :], k=min(len(index), 4 * k + len(exclude_messages)))
        ids = [int(i) for i in found[0]]
        if not ids:
            return []
        rows = self.db.query(f"SELECT * FROM episodes WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))
        scored = []
        for r in rows:
            if r["user_message_id"] in exclude_messages or r["assistant_message_id"] in exclude_messages:
                continue
            score = float(np.frombuffer(r["vector"], dtype=np.float32) @ q)
            if score >= min_score:
                scored.append((_episode(r), score))
        scored.sort(key=lambda t: (math.floor(t[1] / TIE_BUCKET), t[0].created_at), reverse=True)
        return scored[:k]

    def status(self) -> dict:
        indexed = self.db.query_one("SELECT count(*) FROM episodes")[0]
        return {"indexed": indexed, "exchanges": len(self._exchanges()), "rebuilding": self._rebuilding}
