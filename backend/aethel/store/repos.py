import json
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

from .db import Database

Role = Literal["user", "assistant", "system"]
MessageStatus = Literal["complete", "streaming", "stopped", "error"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Conversation(BaseModel):
    id: str
    title: str
    persona_id: str
    created_at: str
    updated_at: str


class Message(BaseModel):
    id: str
    conversation_id: str
    role: Role
    content: str
    status: MessageStatus
    meta: dict
    created_at: str


def _conversation(row) -> Conversation:
    return Conversation(**dict(row))


def _message(row) -> Message:
    data = dict(row)
    data["meta"] = json.loads(data["meta"])
    return Message(**data)


class ConversationRepo:
    def __init__(self, db: Database):
        self.db = db

    def create(self, persona_id: str = "aethel", title: str = "") -> Conversation:
        ts = now_iso()
        conv_id = new_id("conv")
        self.db.execute(
            "INSERT INTO conversations (id, title, persona_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (conv_id, title, persona_id, ts, ts),
        )
        return self.get(conv_id)

    def get(self, conv_id: str) -> Conversation | None:
        row = self.db.query_one("SELECT * FROM conversations WHERE id = ?", (conv_id,))
        return _conversation(row) if row else None

    def list(self) -> list[Conversation]:
        rows = self.db.query("SELECT * FROM conversations ORDER BY updated_at DESC, rowid DESC")
        return [_conversation(r) for r in rows]

    def rename(self, conv_id: str, title: str) -> Conversation | None:
        self.db.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?", (title, now_iso(), conv_id)
        )
        return self.get(conv_id)

    def delete(self, conv_id: str) -> bool:
        return self.db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,)).rowcount > 0

    def touch(self, conv_id: str) -> None:
        self.db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now_iso(), conv_id))


class MessageRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(
        self,
        conversation_id: str,
        role: Role,
        content: str,
        status: MessageStatus = "complete",
        meta: dict | None = None,
    ) -> Message:
        msg_id = new_id("msg")
        self.db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, status, meta, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (msg_id, conversation_id, role, content, status, json.dumps(meta or {}), now_iso()),
        )
        ConversationRepo(self.db).touch(conversation_id)
        return _message(self.db.query_one("SELECT * FROM messages WHERE id = ?", (msg_id,)))

    def update(
        self,
        msg_id: str,
        *,
        content: str | None = None,
        status: MessageStatus | None = None,
        meta: dict | None = None,
    ) -> None:
        sets, params = [], []
        if content is not None:
            sets.append("content = ?")
            params.append(content)
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if meta is not None:
            sets.append("meta = ?")
            params.append(json.dumps(meta))
        if sets:
            self.db.execute(f"UPDATE messages SET {', '.join(sets)} WHERE id = ?", (*params, msg_id))

    def mark_stopped_if_streaming(self, msg_id: str) -> bool:
        """Atomically end a reply that's still marked streaming. False if it
        doesn't exist or already finished."""
        cur = self.db.execute(
            "UPDATE messages SET status = 'stopped' WHERE id = ? AND status = 'streaming'", (msg_id,)
        )
        return cur.rowcount > 0

    def reconcile_interrupted(self) -> int:
        """At startup no turn is running, so any row still marked streaming was
        cut off by a kill or crash. Mark those stopped; returns how many."""
        return self.db.execute("UPDATE messages SET status = 'stopped' WHERE status = 'streaming'").rowcount

    def list(self, conversation_id: str, limit: int | None = None) -> list[Message]:
        if limit is None:
            rows = self.db.query(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY rowid", (conversation_id,)
            )
        else:
            rows = self.db.query(
                "SELECT * FROM (SELECT rowid AS _r, * FROM messages WHERE conversation_id = ?"
                " ORDER BY rowid DESC LIMIT ?) ORDER BY _r",
                (conversation_id, limit),
            )
        return [_message({k: r[k] for k in r.keys() if k != "_r"}) for r in rows]
