"""Thin sqlite3 wrapper: one connection, a lock, numbered .sql migrations."""
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _migrations() -> list[tuple[int, str]]:
    found = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(path.name.split("_", 1)[0])
        found.append((version, path.read_text(encoding="utf-8")))
    return found


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = self._conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                self._conn.execute("INSERT INTO schema_version (version) VALUES (0)")
                current = 0
            else:
                current = row[0]
            for version, sql in _migrations():
                if version > current:
                    self._conn.executescript(
                        f"BEGIN;\n{sql}\nUPDATE schema_version SET version = {version};\nCOMMIT;"
                    )

    def schema_version(self) -> int:
        return self.query_one("SELECT version FROM schema_version")[0]

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Everything inside commits together or not at all (the connection is otherwise in autocommit
        mode). Holds the lock throughout, so other threads' statements wait rather than interleave."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
