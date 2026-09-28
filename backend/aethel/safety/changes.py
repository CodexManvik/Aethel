"""Snapshots files before the agent writes them, so a task can be rolled back."""
from pathlib import Path

from ..store.db import Database
from ..store.repos import new_id, now_iso


class ChangeLog:
    def __init__(self, db: Database):
        self.db = db

    def record_before_write(self, path: str, task_id: str | None) -> str:
        p = Path(path)
        existed = p.is_file()
        before = p.read_bytes() if existed else None
        change_id = new_id("chg")
        self.db.execute(
            "INSERT INTO file_changes (id, task_id, path, existed, before, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (change_id, task_id, str(p), int(existed), before, now_iso()),
        )
        return change_id

    def rollback(self, change_id: str) -> tuple[bool, str]:
        row = self.db.query_one("SELECT * FROM file_changes WHERE id = ?", (change_id,))
        if row is None:
            return False, "Unknown change."
        if row["rolled_back"]:
            return False, "Already rolled back."
        p = Path(row["path"])
        try:
            if row["existed"]:
                p.write_bytes(row["before"])
            elif p.exists():
                p.unlink()
        except OSError as exc:
            return False, f"Couldn't restore {p}: {exc}"
        self.db.execute("UPDATE file_changes SET rolled_back = 1 WHERE id = ?", (change_id,))
        return True, f"Restored {p}"

    def rollback_task(self, task_id: str) -> list[tuple[bool, str]]:
        rows = self.db.query("SELECT id FROM file_changes WHERE task_id = ? ORDER BY rowid DESC", (task_id,))
        return [self.rollback(r["id"]) for r in rows]

    def for_task(self, task_id: str) -> list[dict]:
        rows = self.db.query(
            "SELECT id, path, existed, rolled_back, created_at FROM file_changes WHERE task_id = ? ORDER BY rowid",
            (task_id,),
        )
        return [{**dict(r), "existed": bool(r["existed"]), "rolled_back": bool(r["rolled_back"])} for r in rows]
