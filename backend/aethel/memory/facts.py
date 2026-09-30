"""Semantic memory (Phase 3 spec §2): short facts about the user, and between the user and a persona.
The algorithm is Mem0's (extract, compare with the nearest facts, add/update/delete); this is the store it
runs on: SQLite rows with their embedder vectors, and an append-only log of every change."""
from __future__ import annotations  # FactStore.list shadows the builtin inside the class body

import re
import threading
from typing import Callable, Literal

import numpy as np
from pydantic import BaseModel

from ..store.db import Database
from ..store.repos import new_id, now_iso

MAX_FACT_CHARS = 300
_SCOPE_RE = re.compile(r"^(user|persona:[a-z0-9_-]{1,64})$")


def valid_scope(scope: str) -> bool:
    return bool(_SCOPE_RE.match(scope))


class Fact(BaseModel):
    id: str
    scope: str
    text: str
    source_message_id: str | None
    conversation_id: str | None
    created_at: str
    updated_at: str


class FactEvent(BaseModel):
    id: int
    fact_id: str
    op: Literal["add", "update", "delete"]
    old_text: str | None
    new_text: str | None
    actor: Literal["extractor", "user"]
    source_message_id: str | None
    created_at: str


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:MAX_FACT_CHARS]


class FactStore:
    def __init__(self, db: Database, embed: Callable[[list[str]], np.ndarray]):
        self.db = db
        self.embed = embed
        self._lock = threading.RLock()
        self._vectors: dict[str, np.ndarray] | None = None  # fact id -> vector, loaded on the first search

    @staticmethod
    def _fact(row) -> Fact:
        return Fact(**{k: row[k] for k in Fact.model_fields})

    def get(self, fact_id: str) -> Fact | None:
        row = self.db.query_one("SELECT * FROM facts WHERE id = ?", (fact_id,))
        return self._fact(row) if row else None

    def list(self, scope: str | None = None, q: str | None = None) -> list[Fact]:
        sql, params = "SELECT * FROM facts WHERE 1=1", []
        if scope:
            sql += " AND scope = ?"
            params.append(scope)
        if q:
            sql += " AND lower(text) LIKE ?"
            params.append(f"%{q.lower()}%")
        return [self._fact(r) for r in self.db.query(sql + " ORDER BY updated_at DESC, rowid DESC", tuple(params))]

    def history(self, fact_id: str) -> list[FactEvent]:
        rows = self.db.query("SELECT * FROM fact_events WHERE fact_id = ? ORDER BY id DESC", (fact_id,))
        return [FactEvent(**dict(r)) for r in rows]

    def _event(self, fact_id: str, op: str, old: str | None, new: str | None, actor: str,
               source: str | None) -> None:
        self.db.execute("INSERT INTO fact_events (fact_id, op, old_text, new_text, actor, source_message_id,"
                        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (fact_id, op, old, new, actor, source, now_iso()))

    def _vector(self, text: str) -> np.ndarray:
        return np.asarray(self.embed([text])[0], dtype=np.float32)

    def add(self, scope: str, text: str, *, actor: str, source_message_id: str | None = None,
            conversation_id: str | None = None) -> Fact:
        text = _clean(text)
        vec = self._vector(text)  # before the transaction: an embedder failure writes nothing
        fact_id, ts = new_id("fact"), now_iso()
        with self._lock, self.db.transaction():
            self.db.execute("INSERT INTO facts (id, scope, text, vector, source_message_id, conversation_id,"
                            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (fact_id, scope, text, vec.tobytes(), source_message_id, conversation_id, ts, ts))
            self._event(fact_id, "add", None, text, actor, source_message_id)
            if self._vectors is not None:
                self._vectors[fact_id] = vec
        return self.get(fact_id)

    def update(self, fact_id: str, text: str, *, actor: str, source_message_id: str | None = None) -> Fact | None:
        old = self.get(fact_id)
        if old is None:
            return None
        text = _clean(text)
        vec = self._vector(text)
        with self._lock, self.db.transaction():
            self.db.execute("UPDATE facts SET text = ?, vector = ?, updated_at = ? WHERE id = ?",
                            (text, vec.tobytes(), now_iso(), fact_id))
            self._event(fact_id, "update", old.text, text, actor, source_message_id)
            if self._vectors is not None:
                self._vectors[fact_id] = vec
        return self.get(fact_id)

    def delete(self, fact_id: str, *, actor: str, source_message_id: str | None = None) -> Fact | None:
        """Removes the row (the event log keeps its text); returns what was deleted, for undo."""
        old = self.get(fact_id)
        if old is None:
            return None
        with self._lock, self.db.transaction():
            self.db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            self._event(fact_id, "delete", old.text, None, actor, source_message_id)
            if self._vectors is not None:
                self._vectors.pop(fact_id, None)
        return old

    def search(self, query: str, scopes: list[str], k: int, min_score: float = 0.0) -> list[tuple[Fact, float]]:
        """Brute-force cosine: a personal fact list stays in the thousands, which numpy ranks in microseconds."""
        if not scopes or k <= 0:
            return []
        with self._lock:
            if self._vectors is None:
                self._vectors = {r["id"]: np.frombuffer(r["vector"], dtype=np.float32)
                                 for r in self.db.query("SELECT id, vector FROM facts")}
            rows = {r["id"]: r for r in self.db.query(
                f"SELECT * FROM facts WHERE scope IN ({','.join('?' * len(scopes))})", tuple(scopes))}
            ids = [i for i in rows if i in self._vectors]
            if not ids:
                return []
            sims = np.stack([self._vectors[i] for i in ids]) @ self._vector(query)
        ranked = sorted(zip(ids, map(float, sims)), key=lambda t: t[1], reverse=True)
        return [(self._fact(rows[i]), s) for i, s in ranked[:k] if s >= min_score]
