# Aethel v2 · Phase 0 (Foundation): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the v2 architecture end to end. That means a new `backend/aethel` package (SQLite store, OpenAI-compatible role router with failover and private mode, typed WebSocket events, local auth, keys held in memory), a Tauri shell that launches the backend and keeps API keys in Windows Credential Manager, and a new Paper & Ink React UI. By the end, you can chat with the built-in "Aethel" persona through Groq, and switch to private mode to chat locally.

**Architecture:** FastAPI app factory (`aethel.app:create_app`) with a `Services` container built in the lifespan. Chat turns stream over `/ws/session` as Pydantic-typed events; REST covers CRUD. Every LLM call goes through `RoleRouter`, which walks a per-role failover chain of `OpenAICompatProvider`s (Groq, Gemini, OpenRouter, custom, local llama.cpp). The frontend is rebuilt from scratch: Zustand stores fed by one WebSocket, TanStack Query for REST, and TypeScript event types generated from the backend's JSON Schema.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, `openai` SDK 2.x, sqlite3, pytest + anyio · Tauri 2 (Rust, `keyring` 3, `uuid`) · React 18, Vite 6, Tailwind 4, `motion` 12, Zustand 5, TanStack Query 5, Radix, react-markdown, Vitest + Testing Library, json-schema-to-typescript.

**Spec:** `docs/superpowers/specs/2026-09-23-aethel-v2-design.md` (sections 2, 3, 11, 12, 13, 15 · Phase 0)

## Global Constraints

- Python interpreter on this machine is **`py -3.11`** (global install, no venv). Backend tests run from `backend/`: `py -3.11 -m pytest`.
- Backend binds **`127.0.0.1`** only, port **`8765`** (`AETHEL_PORT` overrides).
- All runtime data under **`~/.aethel/`** (`AETHEL_HOME` overrides; tests always override it).
- Do **not** modify or delete the legacy backend modules in `backend/*.py` (server.py etc.) during Phase 0. They are migrated in later phases. The new code lives only in `backend/aethel/` and `backend/tests/`.
- Do **not** add LiteLLM. Use the official `openai` SDK (spec §3, revised).
- API keys never touch disk in the backend. They're held in memory (`KeyStore`), pushed by the Tauri shell from Windows Credential Manager, or read from env vars (`GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `CUSTOM_API_KEY`) for headless dev.
- Auth: every REST route except `GET /api/health` requires `Authorization: Bearer <token>`. The WebSocket requires `?token=<token>`. `AETHEL_DEV=1` with no `AETHEL_TOKEN` disables auth (dev only).
- Paper & Ink tokens (spec §12.2), verbatim. Light: canvas `#f3efe7`, paper `#fbf9f4`, ink `#1d1b17`, muted `#7a746a`, hairline `#e2dccf`, accent `#c2553a`. Dark: canvas `#1a1815`, paper `#211e1a`, ink `#ece6da`, muted `#9a9285`, hairline `#2c2823`, accent `#e07a5f`.
- Fonts are self-hosted (no Google Fonts at runtime): Instrument Serif (display), Newsreader (persona voice), Inter (UI).
- React stays on **18.3.1**. Frontend package manager is **pnpm**.
- Every commit message ends with the trailer line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work on branch `revamp`.

---

## File map

**Backend (new):**
```
backend/pytest.ini
backend/aethel/__init__.py            version
backend/aethel/__main__.py            `python -m aethel` → uvicorn
backend/aethel/paths.py               ~/.aethel, db path, project paths
backend/aethel/parent_watch.py        exit when the Tauri parent process dies
backend/aethel/app.py                 create_app(services=None)
backend/aethel/services.py            Services container + build_services()
backend/aethel/auth.py                AuthConfig (token / dev mode)
backend/aethel/keys.py                KeyStore (in-memory API keys + env fallback)
backend/aethel/settings.py            AppSettings model + SettingsService
backend/aethel/store/__init__.py
backend/aethel/store/db.py            Database (sqlite3 + migrations)
backend/aethel/store/migrations/001_init.sql
backend/aethel/store/repos.py         Conversation/Message models + repos
backend/aethel/providers/__init__.py
backend/aethel/providers/base.py      ChatMessage, TextDelta, StreamDone, ProviderError, LLMProvider
backend/aethel/providers/catalog.py   provider ids, labels, base URLs
backend/aethel/providers/openai_compat.py  OpenAICompatProvider
backend/aethel/providers/local_llama.py    LocalLlama (port of generation.py lifecycle)
backend/aethel/providers/router.py    RoleRouter, ProviderSwitch, NoProviderAvailable
backend/aethel/chat/__init__.py
backend/aethel/chat/persona.py        built-in Aethel system prompt
backend/aethel/chat/service.py        ChatService (turns, stop, titles)
backend/aethel/api/__init__.py
backend/aethel/api/deps.py            get_services, require_auth
backend/aethel/api/events.py          typed WS events + schema export
backend/aethel/api/ws.py              /ws/session
backend/aethel/api/routes/__init__.py
backend/aethel/api/routes/health.py
backend/aethel/api/routes/keys.py
backend/aethel/api/routes/conversations.py
backend/aethel/api/routes/settings.py
backend/aethel/api/routes/providers.py
backend/tests/conftest.py
backend/tests/fakes.py
backend/tests/test_*.py               one file per task
scripts/gen_event_schema.py           writes frontend_app/src/lib/events.schema.json
```

**Tauri (modified):** `frontend_app/src-tauri/Cargo.toml`, `src/lib.rs`, `tauri.conf.json`, `capabilities/default.json`.

**Frontend (rebuilt):**
```
frontend_app/index.html, package.json, vite.config.ts
frontend_app/src/main.tsx, App.tsx, fonts.ts, vite-env.d.ts
frontend_app/src/test/setup.ts
frontend_app/src/styles/index.css, tokens.css, base.css
frontend_app/src/lib/backend.ts, api.ts, types.ts, ws.ts, session.ts, keys.ts, events.ts, ids.ts
frontend_app/src/lib/events.schema.json (generated), events.gen.ts (generated)
frontend_app/src/stores/ui.ts, session.ts
frontend_app/src/ui/cn.ts, IconButton.tsx, Button.tsx, Switch.tsx, Field.tsx
frontend_app/src/features/shell/AppShell.tsx, Rail.tsx, Boot.tsx, ErrorBoundary.tsx, useSessionEvents.ts
frontend_app/src/features/conversation/*.tsx|ts
frontend_app/src/features/settings/*.tsx|ts
```

**Deleted (frontend):** `src/app/**`, `src/styles/{fonts,globals,tailwind,theme}.css`, `default_shadcn_theme.css`, `canonical_face_model.obj`, `ATTRIBUTIONS.md`, `postcss.config.mjs`.

---

### Task 1: Backend package skeleton, health route, test harness

**Files:**
- Create: `backend/pytest.ini`, `backend/aethel/__init__.py`, `backend/aethel/__main__.py`, `backend/aethel/paths.py`, `backend/aethel/parent_watch.py`, `backend/aethel/app.py`, `backend/aethel/api/__init__.py`, `backend/aethel/api/routes/__init__.py`, `backend/aethel/api/routes/health.py`
- Test: `backend/tests/conftest.py`, `backend/tests/test_health.py`

**Interfaces:**
- Produces: `aethel.paths.aethel_home() -> Path`, `aethel.paths.db_path() -> Path`, `aethel.paths.PROJECT_ROOT: Path`, `aethel.paths.MODELS_DIR: Path`, `aethel.paths.LEGACY_SETTINGS_PATH: Path`; `aethel.app.create_app() -> FastAPI` (extended in Task 4); `aethel.__version__: str`. Test fixture `aethel_home` (autouse) sets `AETHEL_HOME` to a temp dir; fixture `anyio_backend` returns `"asyncio"`.

- [ ] **Step 1: Write the test harness and failing test**

`backend/pytest.ini`:
```ini
[pytest]
testpaths = tests
addopts = -q
markers =
    desktop: needs a real Windows desktop session (run manually)
```

`backend/tests/conftest.py`:
```python
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(autouse=True)
def aethel_home(tmp_path, monkeypatch):
    """Every test gets an isolated ~/.aethel and a clean auth/key environment."""
    home = tmp_path / "aethel_home"
    monkeypatch.setenv("AETHEL_HOME", str(home))
    monkeypatch.setenv("AETHEL_DEV", "1")
    monkeypatch.delenv("AETHEL_TOKEN", raising=False)
    for var in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "CUSTOM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

`backend/tests/test_health.py`:
```python
from fastapi.testclient import TestClient

from aethel import __version__
from aethel.app import create_app


def test_health_reports_ok_and_version():
    with TestClient(create_app()) as client:
        res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "version": __version__}


def test_aethel_home_is_isolated(aethel_home):
    from aethel.paths import aethel_home as home_fn, db_path

    assert home_fn() == aethel_home
    assert aethel_home.is_dir()
    assert db_path() == aethel_home / "aethel.db"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && py -3.11 -m pytest tests/test_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aethel'`

- [ ] **Step 3: Implement the skeleton**

`backend/aethel/__init__.py`:
```python
"""Aethel v2 backend package."""

__version__ = "2.0.0-dev"
```

`backend/aethel/paths.py`:
```python
"""Filesystem locations. Everything user-specific lives under ~/.aethel."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = Path(os.environ.get("MODELS_DIR", str(PROJECT_ROOT / "models")))
LEGACY_SETTINGS_PATH = PROJECT_ROOT / "backend" / "data" / "settings.json"


def aethel_home() -> Path:
    home = Path(os.environ.get("AETHEL_HOME", str(Path.home() / ".aethel")))
    home.mkdir(parents=True, exist_ok=True)
    return home


def db_path() -> Path:
    return aethel_home() / "aethel.db"
```

`backend/aethel/parent_watch.py`:
```python
"""Exit the backend when the process that launched it (the Tauri shell) dies.

Without this, a crashed shell leaves an orphaned backend holding the port.
"""
import os
import threading


def watch_parent(pid: int) -> None:
    if os.name == "nt":
        import ctypes

        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return

        def wait() -> None:
            ctypes.windll.kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
            os._exit(0)
    else:
        import time

        def wait() -> None:
            while True:
                try:
                    os.kill(pid, 0)
                except OSError:
                    os._exit(0)
                time.sleep(2)

    threading.Thread(target=wait, name="parent-watch", daemon=True).start()
```

`backend/aethel/__main__.py`:
```python
import os

import uvicorn

from .parent_watch import watch_parent


def main() -> None:
    parent = os.environ.get("AETHEL_PARENT_PID")
    if parent and parent.isdigit():
        watch_parent(int(parent))
    uvicorn.run(
        "aethel.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=int(os.environ.get("AETHEL_PORT", "8765")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
```

`backend/aethel/api/__init__.py` and `backend/aethel/api/routes/__init__.py`: empty files.

`backend/aethel/api/routes/health.py`:
```python
from fastapi import APIRouter

from ... import __version__

router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__}
```

`backend/aethel/app.py`:
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api.routes import health

ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
]


def create_app() -> FastAPI:
    app = FastAPI(title="Aethel", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    return app
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && py -3.11 -m pytest tests/test_health.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/pytest.ini backend/aethel backend/tests
git commit -m "feat(backend): add aethel v2 package skeleton with health route" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: SQLite store, migrations, conversation and message repos

**Files:**
- Create: `backend/aethel/store/__init__.py` (empty), `backend/aethel/store/db.py`, `backend/aethel/store/migrations/001_init.sql`, `backend/aethel/store/repos.py`
- Test: `backend/tests/test_store.py`

**Interfaces:**
- Consumes: `aethel.paths.db_path()`
- Produces:
  - `Database(path: Path)`, with `.execute(sql, params=()) -> sqlite3.Cursor`, `.query(sql, params=()) -> list[sqlite3.Row]`, `.query_one(sql, params=()) -> sqlite3.Row | None`, `.schema_version() -> int` and `.close()`.
  - `Conversation` and `Message` are Pydantic models:
    - `Conversation`: `id, title, persona_id, created_at, updated_at`
    - `Message`: `id, conversation_id, role, content, status, meta: dict, created_at`
    - `role ∈ {"user","assistant","system"}`
    - `status ∈ {"complete","streaming","stopped","error"}`
  - `ConversationRepo(db)`: `.create(persona_id="aethel", title="") -> Conversation`, `.get(id) -> Conversation | None`, `.list() -> list[Conversation]` (newest `updated_at` first), `.rename(id, title) -> Conversation | None`, `.delete(id) -> bool`, `.touch(id) -> None`.
  - `MessageRepo(db)`: `.add(conversation_id, role, content, status="complete", meta=None) -> Message`, `.update(id, *, content=None, status=None, meta=None) -> None`, `.list(conversation_id, limit=None) -> list[Message]` (chronological; `limit` keeps the LAST n).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_store.py`:
```python
import time

import pytest

from aethel.paths import db_path
from aethel.store.db import Database
from aethel.store.repos import ConversationRepo, MessageRepo


@pytest.fixture
def db():
    database = Database(db_path())
    yield database
    database.close()


def test_migrations_are_idempotent(db):
    assert db.schema_version() == 1
    db.close()
    reopened = Database(db_path())
    assert reopened.schema_version() == 1
    reopened.close()


def test_create_get_and_list_conversations_newest_first(db):
    repo = ConversationRepo(db)
    first = repo.create()
    time.sleep(0.01)
    second = repo.create(persona_id="mira", title="Hello")
    assert repo.get(first.id).persona_id == "aethel"
    assert repo.get("missing") is None
    assert [c.id for c in repo.list()] == [second.id, first.id]


def test_adding_a_message_touches_its_conversation(db):
    convs, msgs = ConversationRepo(db), MessageRepo(db)
    a = convs.create()
    time.sleep(0.01)
    b = convs.create()
    time.sleep(0.01)
    msgs.add(a.id, "user", "hi")
    assert [c.id for c in convs.list()] == [a.id, b.id]


def test_messages_are_chronological_and_limit_keeps_the_tail(db):
    conv = ConversationRepo(db).create()
    repo = MessageRepo(db)
    for i in range(5):
        repo.add(conv.id, "user" if i % 2 == 0 else "assistant", f"m{i}")
    assert [m.content for m in repo.list(conv.id)] == ["m0", "m1", "m2", "m3", "m4"]
    assert [m.content for m in repo.list(conv.id, limit=2)] == ["m3", "m4"]


def test_update_message_content_status_and_meta(db):
    conv = ConversationRepo(db).create()
    repo = MessageRepo(db)
    msg = repo.add(conv.id, "assistant", "", status="streaming")
    repo.update(msg.id, content="done", status="complete", meta={"provider": "groq:x"})
    [stored] = repo.list(conv.id)
    assert (stored.content, stored.status, stored.meta) == ("done", "complete", {"provider": "groq:x"})


def test_rename_and_cascade_delete(db):
    convs, msgs = ConversationRepo(db), MessageRepo(db)
    conv = convs.create()
    msgs.add(conv.id, "user", "hi")
    assert convs.rename(conv.id, "Renamed").title == "Renamed"
    assert convs.rename("missing", "x") is None
    assert convs.delete(conv.id) is True
    assert convs.delete(conv.id) is False
    assert msgs.list(conv.id) == []


def test_invalid_role_is_rejected(db):
    conv = ConversationRepo(db).create()
    with pytest.raises(Exception):
        MessageRepo(db).add(conv.id, "robot", "x")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && py -3.11 -m pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aethel.store'`

- [ ] **Step 3: Implement the store**

`backend/aethel/store/migrations/001_init.sql`:
```sql
CREATE TABLE conversations (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  persona_id TEXT NOT NULL DEFAULT 'aethel',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  content TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'complete'
    CHECK (status IN ('complete', 'streaming', 'stopped', 'error')),
  meta TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id);

CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
```

`backend/aethel/store/db.py`:
```python
"""Thin sqlite3 wrapper: one connection, a lock, numbered .sql migrations."""
import sqlite3
import threading
from pathlib import Path

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

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
```

`backend/aethel/store/repos.py`:
```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && py -3.11 -m pytest tests/test_store.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add backend/aethel/store backend/tests/test_store.py
git commit -m "feat(backend): add SQLite store with migrations and conversation repos" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Settings model and persistence, with legacy import

**Files:**
- Create: `backend/aethel/settings.py`
- Test: `backend/tests/test_settings.py`

**Interfaces:**
- Consumes: `Database` (Task 2), `aethel.paths.LEGACY_SETTINGS_PATH`
- Produces:
  - `ProviderId = Literal["groq","gemini","openrouter","custom","local"]`
  - `RouteEntry(provider: ProviderId, model: str)`
  - `LocalLLMSettings(model_path="", context_size=0, threads=4, gpu_layers=99)`
  - `AppSettings`, with fields `roles: dict[str, list[RouteEntry]]`, `custom_base_url: str`, `private_mode: bool`, `internet: bool`, `local_llm: LocalLLMSettings`, `temperature: float`, `max_tokens: int` and `history_window: int`
  - `SettingsService(db)`, with `.get() -> AppSettings`, `.update(patch: dict) -> AppSettings` (deep-merges dicts, replaces lists, raises `pydantic.ValidationError` on invalid input) and `.import_legacy(path: Path) -> bool`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_settings.py`:
```python
import json

import pytest
from pydantic import ValidationError

from aethel.paths import db_path
from aethel.settings import AppSettings, RouteEntry, SettingsService
from aethel.store.db import Database


@pytest.fixture
def service():
    db = Database(db_path())
    yield SettingsService(db)
    db.close()


def test_defaults_have_a_chain_for_each_role(service):
    s = service.get()
    assert set(s.roles) == {"chat", "agent", "vision"}
    assert s.roles["chat"][0] == RouteEntry(provider="groq", model="llama-3.3-70b-versatile")
    assert s.roles["chat"][-1].provider == "local"
    assert s.private_mode is False
    assert s.local_llm.context_size == 0


def test_update_deep_merges_and_persists(service):
    service.update({"private_mode": True, "local_llm": {"threads": 8}})
    fresh = service.get()
    assert fresh.private_mode is True
    assert fresh.local_llm.threads == 8
    assert fresh.local_llm.gpu_layers == 99


def test_update_replaces_role_lists_wholesale(service):
    service.update({"roles": {"chat": [{"provider": "gemini", "model": "gemini-2.5-flash"}]}})
    s = service.get()
    assert s.roles["chat"] == [RouteEntry(provider="gemini", model="gemini-2.5-flash")]
    assert s.roles["agent"]  # other roles untouched


def test_invalid_provider_is_rejected_and_nothing_saved(service):
    with pytest.raises(ValidationError):
        service.update({"roles": {"chat": [{"provider": "nope", "model": "x"}]}})
    assert service.get() == AppSettings()


def test_legacy_import_runs_once(service, tmp_path):
    legacy = tmp_path / "settings.json"
    legacy.write_text(
        json.dumps({"llm_model_path": "C:/m.gguf", "context_size": 4096, "threads": 6, "gpu_layers": 20}),
        encoding="utf-8",
    )
    assert service.import_legacy(legacy) is True
    local = service.get().local_llm
    assert (local.model_path, local.context_size, local.threads, local.gpu_layers) == ("C:/m.gguf", 4096, 6, 20)
    service.update({"local_llm": {"threads": 2}})
    assert service.import_legacy(legacy) is False
    assert service.get().local_llm.threads == 2


def test_legacy_import_tolerates_missing_file(service, tmp_path):
    assert service.import_legacy(tmp_path / "nope.json") is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && py -3.11 -m pytest tests/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aethel.settings'`

- [ ] **Step 3: Implement the settings**

`backend/aethel/settings.py`:
```python
"""User settings: one validated JSON document in the SQLite settings table."""
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .store.db import Database

ProviderId = Literal["groq", "gemini", "openrouter", "custom", "local"]


class RouteEntry(BaseModel):
    provider: ProviderId
    model: str = Field(min_length=1)


class LocalLLMSettings(BaseModel):
    model_path: str = ""
    context_size: int = Field(default=0, ge=0)
    threads: int = Field(default=4, ge=1)
    gpu_layers: int = Field(default=99, ge=0)


def default_roles() -> dict[str, list[RouteEntry]]:
    # Starting points only: Settings lists each provider's live models.
    return {
        "chat": [
            RouteEntry(provider="groq", model="llama-3.3-70b-versatile"),
            RouteEntry(provider="openrouter", model="google/gemini-2.5-flash"),
            RouteEntry(provider="local", model="local"),
        ],
        "agent": [
            RouteEntry(provider="gemini", model="gemini-2.5-flash"),
            RouteEntry(provider="openrouter", model="google/gemini-2.5-flash"),
            RouteEntry(provider="local", model="local"),
        ],
        "vision": [RouteEntry(provider="gemini", model="gemini-2.5-flash")],
    }


class AppSettings(BaseModel):
    roles: dict[str, list[RouteEntry]] = Field(default_factory=default_roles)
    custom_base_url: str = ""
    private_mode: bool = False
    internet: bool = False
    local_llm: LocalLLMSettings = Field(default_factory=LocalLLMSettings)
    temperature: float = Field(default=0.8, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=16, le=32768)
    history_window: int = Field(default=24, ge=2, le=200)


def _deep_merge(base: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict) and key != "roles":
            _deep_merge(base[key], value)
        elif key == "roles" and isinstance(value, dict):
            base.setdefault("roles", {}).update(value)
        else:
            base[key] = value
    return base


class SettingsService:
    KEY = "app"
    LEGACY_MARKER = "legacy_imported"

    def __init__(self, db: Database):
        self.db = db

    def get(self) -> AppSettings:
        row = self.db.query_one("SELECT value FROM settings WHERE key = ?", (self.KEY,))
        return AppSettings.model_validate_json(row["value"]) if row else AppSettings()

    def update(self, patch: dict) -> AppSettings:
        merged = _deep_merge(self.get().model_dump(), patch)
        settings = AppSettings.model_validate(merged)
        self._put(self.KEY, settings.model_dump_json())
        return settings

    def import_legacy(self, path: Path) -> bool:
        """Copy local-LLM fields from the v1 backend/data/settings.json, once."""
        if self.db.query_one("SELECT 1 FROM settings WHERE key = ?", (self.LEGACY_MARKER,)):
            return False
        if not path.is_file():
            return False
        try:
            legacy = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        local = {
            "model_path": str(legacy.get("llm_model_path") or ""),
            "context_size": int(legacy.get("context_size", 0) or 0),
            "threads": int(legacy.get("threads", 4) or 4),
            "gpu_layers": int(legacy.get("gpu_layers", 99)),
        }
        self.update({"local_llm": local})
        self._put(self.LEGACY_MARKER, "true")
        return True

    def _put(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && py -3.11 -m pytest tests/test_settings.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/aethel/settings.py backend/tests/test_settings.py
git commit -m "feat(backend): add validated app settings with legacy import" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Key store, auth, services container, keys routes

**Files:**
- Create: `backend/aethel/keys.py`, `backend/aethel/auth.py`, `backend/aethel/services.py`, `backend/aethel/api/deps.py`, `backend/aethel/api/routes/keys.py`
- Modify: `backend/aethel/app.py` (full replacement below)
- Test: `backend/tests/test_keys_auth.py`

**Interfaces:**
- Consumes: `Database`, `ConversationRepo`, `MessageRepo`, `SettingsService`, `db_path()`, `LEGACY_SETTINGS_PATH`
- Produces:
  - `KeyStore`, with `.set_many(keys: dict[str, str | None]) -> None`, `.get(provider) -> str | None` and `.status() -> dict[str, bool]`; `KEY_PROVIDERS = ("groq","gemini","openrouter","custom")`
  - `AuthConfig(token: str | None, dev: bool)`, with `.from_env()` and `.check(presented: str | None) -> bool`
  - `Services` dataclass: `db, settings, keys, auth, conversations, messages` (extended in Tasks 7 and 9), plus `.close()`
  - `build_services() -> Services`
  - `create_app(services: Services | None = None) -> FastAPI`
  - `aethel.api.deps.get_services(request) -> Services` and `require_auth` (a FastAPI dependency)
  - Routes: `POST /api/keys` taking `{"keys": {provider: str | null}}` and returning `{provider: bool}`; `GET /api/keys/status` returning `{provider: bool}`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_keys_auth.py`:
```python
import pytest
from fastapi.testclient import TestClient

from aethel.app import create_app
from aethel.auth import AuthConfig
from aethel.keys import KeyStore


def test_keystore_memory_beats_env_and_empty_deletes(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "env-key")
    store = KeyStore()
    assert store.get("groq") == "env-key"
    store.set_many({"groq": "mem-key", "gemini": "g"})
    assert store.get("groq") == "mem-key"
    store.set_many({"groq": None, "gemini": ""})
    assert store.get("groq") == "env-key"
    assert store.get("gemini") is None
    assert store.status() == {"groq": True, "gemini": False, "openrouter": False, "custom": False}


def test_keystore_rejects_unknown_providers():
    with pytest.raises(ValueError):
        KeyStore().set_many({"evil": "x"})


def test_auth_config():
    assert AuthConfig(token=None, dev=True).check(None) is True
    strict = AuthConfig(token="abc", dev=False)
    assert strict.check("abc") is True
    assert strict.check("nope") is False
    assert strict.check(None) is False


def test_auth_from_env_generates_token_when_not_dev(monkeypatch):
    monkeypatch.setenv("AETHEL_DEV", "0")
    cfg = AuthConfig.from_env()
    assert cfg.dev is False and cfg.token and len(cfg.token) >= 32


def test_routes_require_bearer_token(monkeypatch):
    monkeypatch.setenv("AETHEL_DEV", "0")
    monkeypatch.setenv("AETHEL_TOKEN", "t0k")
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/keys/status").status_code == 401
        ok = client.get("/api/keys/status", headers={"Authorization": "Bearer t0k"})
        assert ok.status_code == 200


def test_keys_route_sets_and_reports_status_without_leaking_values():
    with TestClient(create_app()) as client:
        res = client.post("/api/keys", json={"keys": {"groq": "secret-value"}})
        assert res.status_code == 200
        assert res.json()["groq"] is True
        assert "secret-value" not in res.text
        assert client.get("/api/keys/status").json()["groq"] is True
        assert client.post("/api/keys", json={"keys": {"evil": "x"}}).status_code == 422
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && py -3.11 -m pytest tests/test_keys_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aethel.keys'`

- [ ] **Step 3: Implement the key store, auth, services and routes**

`backend/aethel/keys.py`:
```python
"""API keys, held in memory only. The Tauri shell pushes them from the OS
credential store at startup; env vars are a fallback for headless dev."""
import os

KEY_PROVIDERS = ("groq", "gemini", "openrouter", "custom")
ENV_VARS = {
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "custom": "CUSTOM_API_KEY",
}


class KeyStore:
    def __init__(self) -> None:
        self._keys: dict[str, str] = {}

    def set_many(self, keys: dict[str, str | None]) -> None:
        unknown = set(keys) - set(KEY_PROVIDERS)
        if unknown:
            raise ValueError(f"unknown providers: {sorted(unknown)}")
        for provider, value in keys.items():
            if value:
                self._keys[provider] = value
            else:
                self._keys.pop(provider, None)

    def get(self, provider: str) -> str | None:
        return self._keys.get(provider) or os.environ.get(ENV_VARS.get(provider, ""), "") or None

    def status(self) -> dict[str, bool]:
        return {p: self.get(p) is not None for p in KEY_PROVIDERS}
```

`backend/aethel/auth.py`:
```python
import logging
import os
import secrets

log = logging.getLogger("aethel.auth")


class AuthConfig:
    def __init__(self, token: str | None, dev: bool):
        self.token = token
        self.dev = dev

    @classmethod
    def from_env(cls) -> "AuthConfig":
        token = os.environ.get("AETHEL_TOKEN") or None
        dev = os.environ.get("AETHEL_DEV") == "1"
        if token is None and not dev:
            token = secrets.token_urlsafe(32)
            log.warning("AETHEL_TOKEN not set; generated a random token (clients must be given it).")
        if dev and token is None:
            log.warning("AETHEL_DEV=1 with no token: authentication is DISABLED.")
        return cls(token=token, dev=dev)

    def check(self, presented: str | None) -> bool:
        if self.token is None:
            return self.dev
        return presented is not None and secrets.compare_digest(presented, self.token)
```

`backend/aethel/services.py`:
```python
from dataclasses import dataclass

from .auth import AuthConfig
from .keys import KeyStore
from .paths import LEGACY_SETTINGS_PATH, db_path
from .settings import SettingsService
from .store.db import Database
from .store.repos import ConversationRepo, MessageRepo


@dataclass
class Services:
    db: Database
    settings: SettingsService
    keys: KeyStore
    auth: AuthConfig
    conversations: ConversationRepo
    messages: MessageRepo

    def close(self) -> None:
        self.db.close()


def build_services() -> Services:
    db = Database(db_path())
    settings = SettingsService(db)
    settings.import_legacy(LEGACY_SETTINGS_PATH)
    return Services(
        db=db,
        settings=settings,
        keys=KeyStore(),
        auth=AuthConfig.from_env(),
        conversations=ConversationRepo(db),
        messages=MessageRepo(db),
    )
```

`backend/aethel/api/deps.py`:
```python
from fastapi import HTTPException, Request

from ..services import Services


def get_services(request: Request) -> Services:
    return request.app.state.services


def require_auth(request: Request) -> None:
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else None
    if not get_services(request).auth.check(token):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
```

`backend/aethel/api/routes/keys.py`:
```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...services import Services
from ..deps import get_services, require_auth

router = APIRouter(prefix="/api/keys", dependencies=[Depends(require_auth)])


class KeysIn(BaseModel):
    keys: dict[str, str | None]


@router.post("")
def set_keys(body: KeysIn, svc: Services = Depends(get_services)) -> dict[str, bool]:
    try:
        svc.keys.set_many(body.keys)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return svc.keys.status()


@router.get("/status")
def key_status(svc: Services = Depends(get_services)) -> dict[str, bool]:
    return svc.keys.status()
```

`backend/aethel/app.py` (full replacement):
```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api.routes import health, keys
from .services import Services, build_services

ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
]


def create_app(services: Services | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = services or build_services()
        app.state.services = svc
        try:
            yield
        finally:
            svc.close()

    app = FastAPI(title="Aethel", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(keys.router)
    return app
```

- [ ] **Step 4: Run the whole suite to verify it passes**

Run: `cd backend && py -3.11 -m pytest -v`
Expected: all tests pass (Tasks 1–4)

- [ ] **Step 5: Commit**

```bash
git add backend/aethel backend/tests/test_keys_auth.py
git commit -m "feat(backend): add in-memory key store, token auth and services container" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Provider base types and OpenAI-compatible provider

**Files:**
- Create: `backend/aethel/providers/__init__.py` (empty), `backend/aethel/providers/base.py`, `backend/aethel/providers/catalog.py`, `backend/aethel/providers/openai_compat.py`
- Modify: `requirements.txt` (add `openai>=2.0` under "API server" and `pytest` in a new "Dev / tests" section)
- Test: `backend/tests/test_openai_compat.py`

**Interfaces:**
- Consumes: `AppSettings`
- Produces:
  - `ChatMessage(role: Literal["system","user","assistant"], content: str)` (dataclass)
  - `TextDelta(text: str)` and `StreamDone(finish_reason: str | None)`; `StreamEvent = TextDelta | StreamDone`
  - `ProviderError(message, *, retryable: bool, status: int | None = None)`
  - `LLMProvider`, a Protocol with `label: str` and `stream(messages, *, temperature: float, max_tokens: int) -> AsyncIterator[StreamEvent]`
  - `catalog.PROVIDERS: dict[str, ProviderMeta]` (`ProviderMeta(id, label, needs_key, base_url)`) and `catalog.base_url_for(provider: str, settings: AppSettings) -> str`
  - `OpenAICompatProvider(*, provider, base_url, api_key, model, http_client=None, timeout=60.0)`, with `.stream(...)` and `async .list_models() -> list[str]`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_openai_compat.py`:
```python
import json

import httpx
import pytest

from aethel.providers.base import ChatMessage, ProviderError, StreamDone, TextDelta
from aethel.providers.catalog import PROVIDERS, base_url_for
from aethel.providers.openai_compat import OpenAICompatProvider
from aethel.settings import AppSettings

pytestmark = pytest.mark.anyio


def _chunk(content=None, finish=None):
    delta = {} if content is None else {"content": content}
    return "data: " + json.dumps({
        "id": "c1", "object": "chat.completion.chunk", "created": 0, "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }) + "\n\n"


def _provider(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider(
        provider="groq", base_url="https://example.test/v1", api_key="k", model="m", http_client=client
    )


async def test_streams_text_deltas_then_done():
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        body = _chunk("Hel") + _chunk("lo") + _chunk(finish="stop") + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    provider = _provider(handler)
    events = [e async for e in provider.stream(
        [ChatMessage("system", "be nice"), ChatMessage("user", "hi")], temperature=0.5, max_tokens=64
    )]
    assert events == [TextDelta("Hel"), TextDelta("lo"), StreamDone("stop")]
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["messages"] == [
        {"role": "system", "content": "be nice"}, {"role": "user", "content": "hi"}
    ]
    assert seen["body"]["stream"] is True and seen["body"]["max_tokens"] == 64
    assert provider.label == "groq:m"


@pytest.mark.parametrize("status,retryable", [(429, True), (503, True), (401, False), (404, False)])
async def test_http_errors_map_to_provider_error(status, retryable):
    provider = _provider(lambda r: httpx.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(ProviderError) as exc:
        [e async for e in provider.stream([ChatMessage("user", "hi")], temperature=0.5, max_tokens=8)]
    assert exc.value.retryable is retryable
    assert exc.value.status == status


async def test_connection_errors_are_retryable():
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ProviderError) as exc:
        [e async for e in _provider(handler).stream([ChatMessage("user", "hi")], temperature=0.5, max_tokens=8)]
    assert exc.value.retryable is True


async def test_list_models_sorted():
    def handler(request):
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"object": "list", "data": [
            {"id": "b", "object": "model", "created": 0, "owned_by": "x"},
            {"id": "a", "object": "model", "created": 0, "owned_by": "x"},
        ]})

    assert await _provider(handler).list_models() == ["a", "b"]


def test_catalog_base_urls():
    s = AppSettings(custom_base_url="http://my.box/v1")
    assert base_url_for("groq", s) == "https://api.groq.com/openai/v1"
    assert base_url_for("gemini", s).startswith("https://generativelanguage.googleapis.com/")
    assert base_url_for("custom", s) == "http://my.box/v1"
    assert base_url_for("local", s).startswith("http://127.0.0.1:")
    assert PROVIDERS["local"].needs_key is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && py -3.11 -m pytest tests/test_openai_compat.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aethel.providers'`

- [ ] **Step 3: Implement the providers**

`backend/aethel/providers/base.py`:
```python
from dataclasses import dataclass
from typing import AsyncIterator, Literal, Protocol, Union


@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class TextDelta:
    text: str


@dataclass
class StreamDone:
    finish_reason: str | None


StreamEvent = Union[TextDelta, StreamDone]


class ProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class LLMProvider(Protocol):
    label: str

    def stream(
        self, messages: list[ChatMessage], *, temperature: float, max_tokens: int
    ) -> AsyncIterator[StreamEvent]: ...
```

`backend/aethel/providers/catalog.py`:
```python
import os
from dataclasses import dataclass

from ..settings import AppSettings

LOCAL_BASE_URL = os.environ.get("LLAMA_BASE_URL", "http://127.0.0.1:8080/v1")


@dataclass(frozen=True)
class ProviderMeta:
    id: str
    label: str
    needs_key: bool
    base_url: str  # empty = comes from settings (custom)


PROVIDERS: dict[str, ProviderMeta] = {
    "groq": ProviderMeta("groq", "Groq", True, "https://api.groq.com/openai/v1"),
    "gemini": ProviderMeta(
        "gemini", "Google Gemini", True, "https://generativelanguage.googleapis.com/v1beta/openai/"
    ),
    "openrouter": ProviderMeta("openrouter", "OpenRouter", True, "https://openrouter.ai/api/v1"),
    "custom": ProviderMeta("custom", "Custom (OpenAI-compatible)", True, ""),
    "local": ProviderMeta("local", "Local (llama.cpp)", False, LOCAL_BASE_URL),
}


def base_url_for(provider: str, settings: AppSettings) -> str:
    if provider == "custom":
        return settings.custom_base_url
    return PROVIDERS[provider].base_url
```

`backend/aethel/providers/openai_compat.py`:
```python
"""One client for every OpenAI-compatible endpoint (Groq, Gemini, OpenRouter,
custom servers and local llama.cpp)."""
from typing import AsyncIterator

import httpx
import openai
from openai import AsyncOpenAI

from .base import ChatMessage, ProviderError, StreamDone, StreamEvent, TextDelta

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _error_text(exc: openai.APIStatusError) -> str:
    body = exc.body
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
    return str(exc)[:300]


class OpenAICompatProvider:
    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str,
        model: str,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ):
        self.label = f"{provider}:{model}"
        self.model = model
        self._client = AsyncOpenAI(
            base_url=base_url, api_key=api_key, http_client=http_client, timeout=timeout, max_retries=0
        )

    async def stream(
        self, messages: list[ChatMessage], *, temperature: float, max_tokens: int
    ) -> AsyncIterator[StreamEvent]:
        try:
            stream = await self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            finish = None
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.delta is not None and choice.delta.content:
                    yield TextDelta(choice.delta.content)
                if choice.finish_reason:
                    finish = choice.finish_reason
            yield StreamDone(finish)
        except openai.APIStatusError as exc:
            raise ProviderError(
                f"{self.label}: HTTP {exc.status_code}: {_error_text(exc)}",
                retryable=exc.status_code in RETRYABLE_STATUS,
                status=exc.status_code,
            ) from exc
        except openai.APIConnectionError as exc:  # includes APITimeoutError
            raise ProviderError(f"{self.label}: {exc.__class__.__name__}", retryable=True) from exc

    async def list_models(self) -> list[str]:
        try:
            page = await self._client.models.list()
        except openai.APIStatusError as exc:
            raise ProviderError(
                f"{self.label}: HTTP {exc.status_code}: {_error_text(exc)}", retryable=False, status=exc.status_code
            ) from exc
        except openai.APIConnectionError as exc:
            raise ProviderError(f"{self.label}: {exc.__class__.__name__}", retryable=True) from exc
        return sorted(m.id for m in page.data)
```

Add to `requirements.txt` under `# --- API server ---`: a line `openai>=2.0`. At the end, add:
```
# --- Dev / tests ---
pytest
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && py -3.11 -m pytest tests/test_openai_compat.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add backend/aethel/providers requirements.txt backend/tests/test_openai_compat.py
git commit -m "feat(backend): add OpenAI-compatible streaming provider and provider catalog" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Local llama.cpp lifecycle (port of `generation.py`)

**Files:**
- Create: `backend/aethel/providers/local_llama.py`
- Test: `backend/tests/test_local_llama.py`

**Interfaces:**
- Consumes: `LocalLLMSettings`, `MODELS_DIR`, `catalog.LOCAL_BASE_URL`
- Produces:
  - `LocalLLMUnavailable(Exception)`
  - `LocalLlama(settings_provider: Callable[[], LocalLLMSettings], *, base_url: str = LOCAL_BASE_URL, models_dir: Path = MODELS_DIR)`, with these methods:
    - `.find_binary() -> Path | None`
    - `.discover_model() -> tuple[Path | None, Path | None]`
    - `.build_command(binary, model, mmproj, port) -> list[str]`
    - `.is_up() -> bool`
    - `.ensure_running() -> None` (raises `LocalLLMUnavailable`)
    - `.stop() -> None`
    - `.suspend() -> bool` and `.resume() -> bool`
    - `.lifecycle_lock: threading.RLock`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_local_llama.py`:
```python
from pathlib import Path

import pytest

from aethel.providers.local_llama import LocalLLMUnavailable, LocalLlama
from aethel.settings import LocalLLMSettings


def _write(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def _llama(tmp_path, **settings) -> LocalLlama:
    return LocalLlama(lambda: LocalLLMSettings(**settings), models_dir=tmp_path / "models")


def test_discover_prefers_largest_non_mmproj(tmp_path):
    llm_dir = tmp_path / "models" / "llm"
    _write(llm_dir / "small.gguf", 10)
    big = _write(llm_dir / "big.gguf", 50)
    proj = _write(llm_dir / "mmproj-f16.gguf", 100)
    assert _llama(tmp_path).discover_model() == (big, proj)


def test_discover_honours_settings_override(tmp_path):
    _write(tmp_path / "models" / "llm" / "big.gguf", 50)
    chosen = _write(tmp_path / "elsewhere" / "mine.gguf", 5)
    assert _llama(tmp_path, model_path=str(chosen)).discover_model() == (chosen, None)


def test_discover_returns_none_when_empty(tmp_path):
    assert _llama(tmp_path).discover_model() == (None, None)


def test_find_binary_honours_env_override(tmp_path, monkeypatch):
    exe = _write(tmp_path / "bin" / "llama-server.exe", 1)
    monkeypatch.setenv("LLAMA_SERVER_BINARY", str(exe.parent))
    monkeypatch.setattr("shutil.which", lambda name: None)
    found = _llama(tmp_path).find_binary()
    assert found is not None and found.parent == exe.parent


def test_build_command_flags(tmp_path):
    llama = _llama(tmp_path, context_size=4096, threads=6, gpu_layers=20)
    cmd = llama.build_command(Path("llama-server"), Path("m.gguf"), Path("p.gguf"), 8080)
    joined = " ".join(cmd)
    for fragment in ("-m m.gguf", "--port 8080", "-c 4096", "-t 6", "-ngl 20", "--mmproj p.gguf",
                     "--context-shift", "--cache-reuse 256"):
        assert fragment in joined


def test_build_command_omits_ngl_for_cpu(tmp_path):
    cmd = _llama(tmp_path, gpu_layers=0).build_command(Path("s"), Path("m.gguf"), None, 8080)
    assert "-ngl" not in cmd and "--mmproj" not in cmd


def test_ensure_running_explains_missing_model(tmp_path, monkeypatch):
    llama = _llama(tmp_path)
    monkeypatch.setattr(llama, "is_up", lambda: False)
    with pytest.raises(LocalLLMUnavailable, match="models"):
        llama.ensure_running()


def test_ensure_running_is_noop_when_server_up(tmp_path, monkeypatch):
    llama = _llama(tmp_path)
    monkeypatch.setattr(llama, "is_up", lambda: True)
    llama.ensure_running()  # no model on disk, but nothing to spawn
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && py -3.11 -m pytest tests/test_local_llama.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aethel.providers.local_llama'`

- [ ] **Step 3: Implement `LocalLlama`**

`backend/aethel/providers/local_llama.py`:
```python
"""Owns the local llama-server subprocess (ported from v1 generation.py).

Chat history lives in SQLite, not in llama-server, so stopping/respawning the
server (e.g. to lend VRAM to image generation) never loses a conversation.
"""
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import httpx

from ..paths import MODELS_DIR, PROJECT_ROOT
from ..settings import LocalLLMSettings
from .catalog import LOCAL_BASE_URL

log = logging.getLogger("aethel.local_llama")
BINARY_NAME = "llama-server.exe" if os.name == "nt" else "llama-server"
STARTUP_TIMEOUT_S = int(os.environ.get("LLAMA_STARTUP_TIMEOUT", "120"))


class LocalLLMUnavailable(Exception):
    pass


class LocalLlama:
    def __init__(
        self,
        settings_provider: Callable[[], LocalLLMSettings],
        *,
        base_url: str = LOCAL_BASE_URL,
        models_dir: Path = MODELS_DIR,
    ):
        self._settings = settings_provider
        self.base_url = base_url.rstrip("/")
        self.models_dir = models_dir
        self.lifecycle_lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._suspended = False

    # ---- discovery -------------------------------------------------------
    def find_binary(self) -> Path | None:
        override = os.environ.get("LLAMA_SERVER_BINARY")
        if override:
            p = Path(override)
            if p.is_file():
                return p
            if (p / BINARY_NAME).is_file():
                return p / BINARY_NAME
        on_path = shutil.which(BINARY_NAME)
        if on_path:
            return Path(on_path)
        for candidate in (PROJECT_ROOT / "bin" / "llama" / BINARY_NAME, PROJECT_ROOT / "bin" / BINARY_NAME):
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _scan(directory: Path) -> tuple[Path | None, Path | None]:
        if not directory.is_dir():
            return None, None
        main, proj, main_size = None, None, -1
        for f in sorted(directory.glob("*.gguf")):
            if "mmproj" in f.name.lower():
                proj = f
                continue
            size = f.stat().st_size
            if size > main_size:
                main, main_size = f, size
        return main, proj

    def discover_model(self) -> tuple[Path | None, Path | None]:
        override = self._settings().model_path
        if override and Path(override).is_file():
            chosen = Path(override)
            proj = next((s for s in sorted(chosen.parent.glob("*.gguf")) if "mmproj" in s.name.lower()), None)
            return chosen, proj
        return self._scan(self.models_dir / "llm")

    # ---- process ----------------------------------------------------------
    def _port(self) -> int:
        match = re.search(r":(\d+)", self.base_url)
        return int(match.group(1)) if match else 8080

    def build_command(self, binary: Path, model: Path, mmproj: Path | None, port: int) -> list[str]:
        s = self._settings()
        cmd = [
            str(binary), "-m", str(model), "--port", str(port),
            "-c", str(s.context_size), "-t", str(s.threads),
            "--context-shift", "--cache-reuse", "256", "--log-disable",
        ]
        if s.gpu_layers > 0:
            cmd += ["-ngl", str(s.gpu_layers)]
        if mmproj is not None:
            cmd += ["--mmproj", str(mmproj)]
        return cmd

    def is_up(self) -> bool:
        try:
            return httpx.get(f"{self.base_url}/models", timeout=1.5).status_code == 200
        except httpx.HTTPError:
            return False

    def ensure_running(self) -> None:
        with self.lifecycle_lock:
            if self.is_up():
                return
            model, mmproj = self.discover_model()
            if model is None:
                raise LocalLLMUnavailable(
                    f"No local model found. Put a .gguf file in {self.models_dir / 'llm'} "
                    "or choose one in Settings → Local model."
                )
            binary = self.find_binary()
            if binary is None:
                raise LocalLLMUnavailable(
                    "llama-server not found. Install it (scripts/install.ps1) or set LLAMA_SERVER_BINARY."
                )
            port = self._port()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    raise LocalLLMUnavailable(f"Port {port} is busy but is not answering as llama-server.")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            log.info("starting llama-server: %s", model.name)
            self._process = subprocess.Popen(
                self.build_command(binary, model, mmproj, port),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
            )
            deadline = time.monotonic() + STARTUP_TIMEOUT_S
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    code = self._process.returncode
                    self._process = None
                    raise LocalLLMUnavailable(f"llama-server exited during startup (code {code}).")
                if self.is_up():
                    return
                time.sleep(1)
            self.stop()
            raise LocalLLMUnavailable(f"llama-server did not become ready within {STARTUP_TIMEOUT_S}s.")

    def stop(self) -> None:
        with self.lifecycle_lock:
            proc, self._process = self._process, None
            if proc is None:
                return
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def suspend(self) -> bool:
        with self.lifecycle_lock:
            if self._process is None:
                return False
            self.stop()
            self._suspended = True
            return True

    def resume(self) -> bool:
        with self.lifecycle_lock:
            if not self._suspended:
                return True
            try:
                self.ensure_running()
            except LocalLLMUnavailable as exc:
                log.error("resume failed: %s", exc)
                return False
            self._suspended = False
            return True
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && py -3.11 -m pytest tests/test_local_llama.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add backend/aethel/providers/local_llama.py backend/tests/test_local_llama.py
git commit -m "feat(backend): port local llama-server lifecycle into LocalLlama" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Role router with failover and private mode

**Files:**
- Create: `backend/aethel/providers/router.py`, `backend/tests/fakes.py`
- Modify: `backend/aethel/services.py` (full replacement below)
- Test: `backend/tests/test_router.py`

**Interfaces:**
- Consumes: `SettingsService`, `KeyStore`, `LocalLlama`/`LocalLLMUnavailable`, `OpenAICompatProvider`, `base_url_for`, `ProviderError`, `ChatMessage`, `StreamEvent`
- Produces:
  - `ProviderFactory = Callable[[RouteEntry, str, AppSettings], LLMProvider]` (entry, api_key, settings)
  - `default_provider_factory`
  - `ProviderSwitch(role, from_label, to_label, reason)`
  - `NoProviderAvailable(Exception)`
  - `RoleRouter(*, settings, keys, local, factory)`, with `.chain(role) -> list[RouteEntry]` and `async .stream(role, messages, *, on_switch=None) -> AsyncIterator[StreamEvent]`
  - `Services` gains `local_llm`, `router` and `provider_factory`
  - `build_services(*, provider_factory=None, local_llm=AUTO)`
  - Test fakes: `FakeProvider`, `FakeLocal`, `factory_from(mapping)`

- [ ] **Step 1: Write the fakes and the failing tests**

`backend/tests/fakes.py`:
```python
import anyio

from aethel.providers.base import ProviderError, StreamDone, TextDelta
from aethel.providers.local_llama import LocalLLMUnavailable


class FakeProvider:
    """Scripted LLM. `error_at=None` + `error` set => fails before any token;
    `error_at=i` => fails right before chunk i."""

    def __init__(self, label="fake:model", chunks=("Hello", " there"), error=None, error_at=None,
                 delay=0.0, models=("model-a", "model-b")):
        self.label = label
        self.chunks = list(chunks)
        self.error = error
        self.error_at = error_at
        self.delay = delay
        self.models = list(models)
        self.calls = []

    async def stream(self, messages, *, temperature, max_tokens):
        self.calls.append(list(messages))
        if self.error is not None and self.error_at is None:
            raise self.error
        for i, chunk in enumerate(self.chunks):
            if self.error is not None and self.error_at == i:
                raise self.error
            if self.delay:
                await anyio.sleep(self.delay)
            yield TextDelta(chunk)
        yield StreamDone("stop")

    async def list_models(self):
        return self.models


class FakeLocal:
    def __init__(self, up=True, fail=None):
        self.up = up
        self.fail = fail
        self.ensure_calls = 0

    def ensure_running(self):
        self.ensure_calls += 1
        if self.fail:
            raise LocalLLMUnavailable(self.fail)

    def is_up(self):
        return self.up

    def stop(self):
        pass


def factory_from(mapping):
    """mapping: {"groq:model": FakeProvider, ...}; records calls in factory.used."""
    def factory(entry, api_key, settings):
        factory.used.append((entry.provider, entry.model, api_key))
        return mapping[f"{entry.provider}:{entry.model}"]

    factory.used = []
    return factory


def retryable(msg="rate limited"):
    return ProviderError(msg, retryable=True, status=429)


def fatal(msg="bad key"):
    return ProviderError(msg, retryable=False, status=401)
```

`backend/tests/test_router.py`:
```python
import pytest

from aethel.keys import KeyStore
from aethel.paths import db_path
from aethel.providers.base import ChatMessage, ProviderError, TextDelta
from aethel.providers.router import NoProviderAvailable, RoleRouter
from aethel.settings import SettingsService
from aethel.store.db import Database
from tests.fakes import FakeLocal, FakeProvider, factory_from, fatal, retryable

pytestmark = pytest.mark.anyio
MSGS = [ChatMessage("user", "hi")]


@pytest.fixture
def settings():
    db = Database(db_path())
    svc = SettingsService(db)
    svc.update({"roles": {"chat": [
        {"provider": "groq", "model": "g"},
        {"provider": "openrouter", "model": "o"},
        {"provider": "local", "model": "local"},
    ]}})
    yield svc
    db.close()


def _keys(**kv):
    store = KeyStore()
    store.set_many(kv)
    return store


async def _collect(router, role="chat", switches=None):
    async def on_switch(sw):
        switches.append(sw)
    text = []
    async for ev in router.stream(role, MSGS, on_switch=on_switch if switches is not None else None):
        if isinstance(ev, TextDelta):
            text.append(ev.text)
    return "".join(text)


async def test_first_provider_answers(settings):
    factory = factory_from({"groq:g": FakeProvider(chunks=["hi", "!"])})
    router = RoleRouter(settings=settings, keys=_keys(groq="kg"), local=FakeLocal(), factory=factory)
    switches = []
    assert await _collect(router, switches=switches) == "hi!"
    assert switches == []
    assert factory.used == [("groq", "g", "kg")]


async def test_retryable_failure_before_tokens_fails_over(settings):
    factory = factory_from({
        "groq:g": FakeProvider(label="groq:g", error=retryable()),
        "openrouter:o": FakeProvider(label="openrouter:o", chunks=["ok"]),
    })
    router = RoleRouter(settings=settings, keys=_keys(groq="a", openrouter="b"), local=FakeLocal(), factory=factory)
    switches = []
    assert await _collect(router, switches=switches) == "ok"
    assert [(s.from_label, s.to_label) for s in switches] == [("groq:g", "openrouter:o")]
    assert "rate limited" in switches[0].reason


async def test_failure_after_tokens_is_raised(settings):
    factory = factory_from({"groq:g": FakeProvider(chunks=["a", "b"], error=retryable(), error_at=1)})
    router = RoleRouter(settings=settings, keys=_keys(groq="a", openrouter="b"), local=FakeLocal(), factory=factory)
    with pytest.raises(ProviderError):
        await _collect(router)


async def test_non_retryable_failure_is_raised(settings):
    factory = factory_from({"groq:g": FakeProvider(error=fatal())})
    router = RoleRouter(settings=settings, keys=_keys(groq="a", openrouter="b"), local=FakeLocal(), factory=factory)
    with pytest.raises(ProviderError, match="bad key"):
        await _collect(router)


async def test_entries_without_keys_are_skipped_silently(settings):
    factory = factory_from({"openrouter:o": FakeProvider(chunks=["x"])})
    router = RoleRouter(settings=settings, keys=_keys(openrouter="b"), local=FakeLocal(), factory=factory)
    switches = []
    assert await _collect(router, switches=switches) == "x"
    assert switches == []


async def test_local_used_last_and_started(settings):
    local = FakeLocal()
    factory = factory_from({"local:local": FakeProvider(chunks=["local!"])})
    router = RoleRouter(settings=settings, keys=_keys(), local=local, factory=factory)
    assert await _collect(router) == "local!"
    assert local.ensure_calls == 1
    assert factory.used == [("local", "local", "sk-local")]


async def test_private_mode_uses_only_local(settings):
    settings.update({"private_mode": True})
    factory = factory_from({"local:local": FakeProvider(chunks=["private"])})
    router = RoleRouter(settings=settings, keys=_keys(groq="a", openrouter="b"), local=FakeLocal(), factory=factory)
    assert await _collect(router) == "private"
    assert [u[0] for u in factory.used] == ["local"]


async def test_private_mode_adds_local_when_chain_has_none(settings):
    settings.update({"private_mode": True, "roles": {"vision": [{"provider": "gemini", "model": "v"}]}})
    router = RoleRouter(settings=settings, keys=_keys(), local=FakeLocal(), factory=factory_from({}))
    assert [e.provider for e in router.chain("vision")] == ["local"]


async def test_everything_failing_raises_no_provider_with_reasons(settings):
    router = RoleRouter(settings=settings, keys=_keys(), local=FakeLocal(fail="no model found"),
                        factory=factory_from({}))
    with pytest.raises(NoProviderAvailable) as exc:
        await _collect(router)
    text = str(exc.value)
    assert "groq:g: no API key" in text and "no model found" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && py -3.11 -m pytest tests/test_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aethel.providers.router'`

- [ ] **Step 3: Implement the router and wire the services**

`backend/aethel/providers/router.py`:
```python
"""Picks a provider per role, walking the failover chain from Settings."""
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable

import anyio

from ..keys import KeyStore
from ..settings import AppSettings, RouteEntry, SettingsService
from .base import ChatMessage, LLMProvider, ProviderError, StreamEvent
from .catalog import base_url_for
from .openai_compat import OpenAICompatProvider

ProviderFactory = Callable[[RouteEntry, str, AppSettings], LLMProvider]
LOCAL_API_KEY = "sk-local"


def default_provider_factory(entry: RouteEntry, api_key: str, settings: AppSettings) -> LLMProvider:
    return OpenAICompatProvider(
        provider=entry.provider,
        base_url=base_url_for(entry.provider, settings),
        api_key=api_key,
        model=entry.model,
    )


@dataclass
class ProviderSwitch:
    role: str
    from_label: str
    to_label: str
    reason: str


class NoProviderAvailable(Exception):
    pass


class RoleRouter:
    def __init__(self, *, settings: SettingsService, keys: KeyStore, local, factory: ProviderFactory | None = None):
        self.settings = settings
        self.keys = keys
        self.local = local
        self.factory = factory or default_provider_factory

    def chain(self, role: str) -> list[RouteEntry]:
        s = self.settings.get()
        entries = list(s.roles.get(role, []))
        if s.private_mode:
            entries = [e for e in entries if e.provider == "local"] or [RouteEntry(provider="local", model="local")]
        return entries

    async def stream(
        self,
        role: str,
        messages: list[ChatMessage],
        *,
        on_switch: Callable[[ProviderSwitch], Awaitable[None]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        s = self.settings.get()
        errors: list[str] = []
        failed_label: str | None = None
        for entry in self.chain(role):
            label = f"{entry.provider}:{entry.model}"
            if entry.provider == "local":
                if self.local is None:
                    errors.append(f"{label}: local models are not available")
                    continue
                try:
                    await anyio.to_thread.run_sync(self.local.ensure_running)
                except Exception as exc:  # LocalLLMUnavailable or OS errors
                    errors.append(f"{label}: {exc}")
                    continue
                api_key = LOCAL_API_KEY
            else:
                api_key = self.keys.get(entry.provider)
                if not api_key:
                    errors.append(f"{label}: no API key")
                    continue
            provider = self.factory(entry, api_key, s)
            if failed_label is not None and on_switch is not None:
                await on_switch(ProviderSwitch(role, failed_label, label, errors[-1]))
            started = False
            try:
                async for event in provider.stream(messages, temperature=s.temperature, max_tokens=s.max_tokens):
                    started = True
                    yield event
                return
            except ProviderError as exc:
                if started or not exc.retryable:
                    raise
                errors.append(str(exc))
                failed_label = label
        raise NoProviderAvailable("; ".join(errors) or f"No providers configured for role '{role}'.")
```

`backend/aethel/services.py` (full replacement):
```python
from dataclasses import dataclass

from .auth import AuthConfig
from .keys import KeyStore
from .paths import LEGACY_SETTINGS_PATH, db_path
from .providers.local_llama import LocalLlama
from .providers.router import ProviderFactory, RoleRouter, default_provider_factory
from .settings import SettingsService
from .store.db import Database
from .store.repos import ConversationRepo, MessageRepo

AUTO = object()


@dataclass
class Services:
    db: Database
    settings: SettingsService
    keys: KeyStore
    auth: AuthConfig
    conversations: ConversationRepo
    messages: MessageRepo
    local_llm: object | None
    router: RoleRouter
    provider_factory: ProviderFactory

    def close(self) -> None:
        if self.local_llm is not None:
            self.local_llm.stop()
        self.db.close()


def build_services(*, provider_factory: ProviderFactory | None = None, local_llm=AUTO) -> Services:
    db = Database(db_path())
    settings = SettingsService(db)
    settings.import_legacy(LEGACY_SETTINGS_PATH)
    keys = KeyStore()
    local = LocalLlama(lambda: settings.get().local_llm) if local_llm is AUTO else local_llm
    factory = provider_factory or default_provider_factory
    return Services(
        db=db,
        settings=settings,
        keys=keys,
        auth=AuthConfig.from_env(),
        conversations=ConversationRepo(db),
        messages=MessageRepo(db),
        local_llm=local,
        router=RoleRouter(settings=settings, keys=keys, local=local, factory=factory),
        provider_factory=factory,
    )
```

Create `backend/tests/__init__.py` (empty) so `from tests.fakes import ...` resolves.

- [ ] **Step 4: Run the whole suite to verify it passes**

Run: `cd backend && py -3.11 -m pytest -v`
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add backend/aethel backend/tests
git commit -m "feat(backend): add role router with failover chain and private mode" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Typed WebSocket events and schema export

**Files:**
- Create: `backend/aethel/api/events.py`, `scripts/gen_event_schema.py`, `frontend_app/src/lib/events.schema.json` (generated by the script)
- Test: `backend/tests/test_events.py`

**Interfaces:**
- Produces:
  - Server event models, all with a required literal `type`:
    - `MessageStart(conversation_id, message_id, user_message_id, client_id: str | None, role="assistant")`
    - `Token(message_id, text)`
    - `MessageEnd(message_id, status: "complete"|"stopped"|"error")`
    - `ProviderSwitched(role, from_provider, to_provider, reason)`
    - `ConversationUpdated(conversation_id, title)`
    - `ErrorEvent(message, code: "no_provider"|"provider_error"|"bad_request"|"internal", message_id: str | None)`
  - Client event models:
    - `UserMessage(conversation_id, text: 1..20000 chars, client_id: str | None)`
    - `StopGeneration(message_id)`
  - `server_event_adapter`, `client_event_adapter` (both `TypeAdapter`s)
  - `export_schema() -> dict`
  - `SCHEMA_PATH` (`<repo>/frontend_app/src/lib/events.schema.json`)

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_events.py`:
```python
import json

import pytest
from pydantic import ValidationError

from aethel.api.events import (
    SCHEMA_PATH, ErrorEvent, MessageStart, StopGeneration, Token, UserMessage,
    client_event_adapter, export_schema, server_event_adapter,
)


def test_server_events_round_trip_with_type():
    ev = Token(message_id="m1", text="hi")
    raw = ev.model_dump_json()
    assert json.loads(raw)["type"] == "token"
    assert server_event_adapter.validate_json(raw) == ev


def test_client_events_parse_by_discriminator():
    assert isinstance(
        client_event_adapter.validate_json('{"type":"user_message","conversation_id":"c","text":"hi"}'), UserMessage
    )
    assert isinstance(client_event_adapter.validate_json('{"type":"stop_generation","message_id":"m"}'), StopGeneration)


@pytest.mark.parametrize("raw", [
    '{"type":"nope"}',
    '{"type":"user_message","conversation_id":"c","text":""}',
    '{"type":"user_message","conversation_id":"c","text":"hi","extra":1}',
])
def test_invalid_client_events_rejected(raw):
    with pytest.raises(ValidationError):
        client_event_adapter.validate_json(raw)


def test_schema_marks_type_required_on_every_event():
    defs = export_schema()["$defs"]
    for name in ("MessageStart", "Token", "MessageEnd", "ProviderSwitched", "ConversationUpdated",
                 "ErrorEvent", "UserMessage", "StopGeneration"):
        assert "type" in defs[name]["required"], name
    assert "client_id" in defs["MessageStart"]["required"]


def test_committed_schema_is_up_to_date():
    expected = json.dumps(export_schema(), indent=2, sort_keys=True) + "\n"
    assert SCHEMA_PATH.read_text(encoding="utf-8") == expected, (
        "Run: py -3.11 scripts/gen_event_schema.py  (then pnpm gen:types in frontend_app)"
    )


def test_defaults_serialise():
    ev = MessageStart(conversation_id="c", message_id="m", user_message_id="u", client_id=None)
    assert json.loads(ev.model_dump_json())["role"] == "assistant"
    assert ErrorEvent(message="x", code="internal").message_id is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && py -3.11 -m pytest tests/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aethel.api.events'`

- [ ] **Step 3: Implement the events and the generator**

`backend/aethel/api/events.py`:
```python
"""The WebSocket protocol. The frontend's TypeScript types are generated from
this file: py -3.11 scripts/gen_event_schema.py && (cd frontend_app && pnpm gen:types)"""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ..paths import PROJECT_ROOT

SCHEMA_PATH = PROJECT_ROOT / "frontend_app" / "src" / "lib" / "events.schema.json"


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


# ---- server → client ------------------------------------------------------
class MessageStart(Event):
    type: Literal["message_start"] = "message_start"
    conversation_id: str
    message_id: str
    user_message_id: str
    client_id: str | None = None
    role: Literal["assistant"] = "assistant"


class Token(Event):
    type: Literal["token"] = "token"
    message_id: str
    text: str


class MessageEnd(Event):
    type: Literal["message_end"] = "message_end"
    message_id: str
    status: Literal["complete", "stopped", "error"]


class ProviderSwitched(Event):
    type: Literal["provider_switched"] = "provider_switched"
    role: str
    from_provider: str
    to_provider: str
    reason: str


class ConversationUpdated(Event):
    type: Literal["conversation_updated"] = "conversation_updated"
    conversation_id: str
    title: str


class ErrorEvent(Event):
    type: Literal["error"] = "error"
    message: str
    code: Literal["no_provider", "provider_error", "bad_request", "internal"]
    message_id: str | None = None


ServerEvent = Annotated[
    Union[MessageStart, Token, MessageEnd, ProviderSwitched, ConversationUpdated, ErrorEvent],
    Field(discriminator="type"),
]


# ---- client → server ------------------------------------------------------
class UserMessage(Event):
    type: Literal["user_message"] = "user_message"
    conversation_id: str
    text: str = Field(min_length=1, max_length=20000)
    client_id: str | None = None


class StopGeneration(Event):
    type: Literal["stop_generation"] = "stop_generation"
    message_id: str


ClientEvent = Annotated[Union[UserMessage, StopGeneration], Field(discriminator="type")]

server_event_adapter: TypeAdapter = TypeAdapter(ServerEvent)
client_event_adapter: TypeAdapter = TypeAdapter(ClientEvent)


class AethelProtocol(BaseModel):
    server: ServerEvent
    client: ClientEvent


def export_schema() -> dict:
    return AethelProtocol.model_json_schema(mode="serialization")
```

`scripts/gen_event_schema.py`:
```python
"""Regenerate frontend_app/src/lib/events.schema.json from backend event models."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from aethel.api.events import SCHEMA_PATH, export_schema  # noqa: E402

SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
SCHEMA_PATH.write_text(json.dumps(export_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"wrote {SCHEMA_PATH}")
```

Run it: `py -3.11 scripts/gen_event_schema.py`
Expected: `wrote ...\frontend_app\src\lib\events.schema.json`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && py -3.11 -m pytest tests/test_events.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add backend/aethel/api/events.py backend/tests/test_events.py scripts/gen_event_schema.py frontend_app/src/lib/events.schema.json
git commit -m "feat(backend): add typed websocket event protocol with JSON schema export" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Chat service and the `/ws/session` WebSocket

**Files:**
- Create: `backend/aethel/chat/__init__.py` (empty), `backend/aethel/chat/persona.py`, `backend/aethel/chat/service.py`, `backend/aethel/api/ws.py`
- Modify: `backend/aethel/services.py` (add the `chat` field and its construction; full replacement below), `backend/aethel/app.py` (include the ws router)
- Test: `backend/tests/test_chat_ws.py`

**Interfaces:**
- Consumes:
  - `RoleRouter.stream`, `NoProviderAvailable`, `ProviderSwitch`, `ProviderError`, `TextDelta`, `ChatMessage`
  - the repos and `SettingsService`
  - events from Task 8
- Produces:
  - `persona.system_prompt(now: datetime | None = None) -> str`
  - `service.make_title(text: str) -> str`
  - `ChatService(*, conversations, messages, router, settings)`, with `.start_turn(ev: UserMessage, emit: Emit) -> None`, `.stop(message_id: str) -> bool` and `async .wait_idle() -> None`
  - `Emit = Callable[[BaseModel], Awaitable[None]]`
  - The WS endpoint `/ws/session?token=`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_chat_ws.py`:
```python
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aethel.app import create_app
from aethel.chat.persona import system_prompt
from aethel.chat.service import make_title
from aethel.services import build_services
from tests.fakes import FakeLocal, FakeProvider, factory_from


def _client(providers, keys=None, **settings_patch):
    services = build_services(provider_factory=factory_from(providers), local_llm=FakeLocal(fail="no local model"))
    services.keys.set_many(keys if keys is not None else {"groq": "k"})
    services.settings.update({"roles": {"chat": [{"provider": "groq", "model": "g"}]}, **settings_patch})
    return TestClient(create_app(services)), services


def _receive_until_end(ws):
    events = []
    while True:
        ev = ws.receive_json()
        events.append(ev)
        if ev["type"] == "message_end":
            return events


def test_turn_streams_persists_and_titles():
    provider = FakeProvider(chunks=["Hel", "lo!"])
    client, svc = _client({"groq:g": provider})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": "hi there", "client_id": "c1"})
            events = _receive_until_end(ws)
    types = [e["type"] for e in events]
    assert types == ["message_start", "conversation_updated", "token", "token", "message_end"]
    start = events[0]
    assert start["client_id"] == "c1"
    assert events[1]["title"] == "hi there"
    assert events[-1]["status"] == "complete"
    stored = [(m.role, m.content, m.status) for m in svc.messages.list(conv["id"])]
    assert stored == [("user", "hi there", "complete"), ("assistant", "Hello!", "complete")]
    sent = provider.calls[0]
    assert sent[0].role == "system" and "Aethel" in sent[0].content
    assert [(m.role, m.content) for m in sent[1:]] == [("user", "hi there")]


def test_history_is_included_on_second_turn():
    provider = FakeProvider(chunks=["ok"])
    client, _ = _client({"groq:g": provider})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            for text in ("first", "second"):
                ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": text})
                _receive_until_end(ws)
    assert [(m.role, m.content) for m in provider.calls[1][1:]] == [
        ("user", "first"), ("assistant", "ok"), ("user", "second")
    ]


def test_no_provider_emits_error_and_marks_message():
    client, svc = _client({}, keys={})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": "hi"})
            events = _receive_until_end(ws)
    error = next(e for e in events if e["type"] == "error")
    assert error["code"] == "no_provider" and "no API key" in error["message"]
    assert events[-1]["status"] == "error"
    assert svc.messages.list(conv["id"])[-1].status == "error"


def test_stop_generation_keeps_partial_text():
    client, svc = _client({"groq:g": FakeProvider(chunks=["a", "b", "c"], delay=0.3)})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": "go"})
            start = ws.receive_json()
            ws.receive_json()  # conversation_updated
            assert ws.receive_json() == {"type": "token", "message_id": start["message_id"], "text": "a"}
            ws.send_json({"type": "stop_generation", "message_id": start["message_id"]})
            end = ws.receive_json()
    assert end == {"type": "message_end", "message_id": start["message_id"], "status": "stopped"}
    assert svc.messages.list(conv["id"])[-1].content == "a"


def test_unknown_conversation_and_bad_json_are_reported():
    client, _ = _client({"groq:g": FakeProvider()})
    with client:
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text("{not json")
            assert ws.receive_json()["code"] == "bad_request"
            ws.send_json({"type": "user_message", "conversation_id": "missing", "text": "hi"})
            assert ws.receive_json()["code"] == "bad_request"


def test_websocket_rejects_bad_token(monkeypatch):
    monkeypatch.setenv("AETHEL_DEV", "0")
    monkeypatch.setenv("AETHEL_TOKEN", "right")
    client, _ = _client({"groq:g": FakeProvider()})
    with client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/session?token=wrong") as ws:
                ws.receive_json()
        with client.websocket_connect("/ws/session?token=right"):
            pass


def test_make_title_and_prompt():
    assert make_title("  hello\n  world  ") == "hello world"
    long = make_title("x" * 100)
    assert len(long) == 48 and long.endswith("…")
    assert "Current local time" in system_prompt()
```

(`/api/conversations` is added in Task 10. Until then this test module will fail on that route, which is expected. **Implement Task 9's code, then complete Task 10 Step 3 before running Step 4 of this task**, or run only the tests that don't create a conversation. The simplest order: do Task 9 Steps 1–3, then Task 10 Steps 1–3, then run both test files.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && py -3.11 -m pytest tests/test_chat_ws.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aethel.chat'`

- [ ] **Step 3: Implement the persona prompt, chat service and WebSocket**

`backend/aethel/chat/persona.py`:
```python
"""The built-in 'Aethel' persona (Phase 5 replaces this with persona folders)."""
from datetime import datetime

AETHEL_PERSONA = """
You are Aethel, a warm, perceptive companion and a capable assistant who lives on the user's computer.
Talk like a thoughtful friend: natural, concise, and specific. Match the user's energy and language.
Never pretend to have done something you haven't. If you don't know, say so plainly.
Use Markdown only when structure genuinely helps (steps, code, tables); otherwise write plain prose.
"""


def system_prompt(now: datetime | None = None) -> str:
    now = now or datetime.now().astimezone()
    return AETHEL_PERSONA.strip() + f"\n\nCurrent local time: {now:%A %d %B %Y, %H:%M}."
```

`backend/aethel/chat/service.py`:
```python
"""Runs chat turns: persist → stream from the router → persist → emit events."""
import asyncio
import logging
import re
from typing import Awaitable, Callable

from pydantic import BaseModel

from ..api.events import ConversationUpdated, ErrorEvent, MessageEnd, MessageStart, ProviderSwitched, Token, UserMessage
from ..providers.base import ChatMessage, ProviderError, TextDelta
from ..providers.router import NoProviderAvailable, ProviderSwitch, RoleRouter
from ..settings import SettingsService
from ..store.repos import ConversationRepo, MessageRepo
from .persona import system_prompt

log = logging.getLogger("aethel.chat")
Emit = Callable[[BaseModel], Awaitable[None]]
TITLE_MAX = 48


def make_title(text: str) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= TITLE_MAX else flat[: TITLE_MAX - 1].rstrip() + "…"


class ChatService:
    def __init__(self, *, conversations: ConversationRepo, messages: MessageRepo, router: RoleRouter,
                 settings: SettingsService):
        self.conversations = conversations
        self.messages = messages
        self.router = router
        self.settings = settings
        self._active: dict[str, asyncio.Task] = {}
        self._tasks: set[asyncio.Task] = set()

    def start_turn(self, event: UserMessage, emit: Emit) -> None:
        task = asyncio.create_task(self._turn(event, emit))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def stop(self, message_id: str) -> bool:
        task = self._active.get(message_id)
        if task is None:
            return False
        task.cancel()
        return True

    async def wait_idle(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _turn(self, event: UserMessage, emit: Emit) -> None:
        conv = self.conversations.get(event.conversation_id)
        if conv is None:
            await emit(ErrorEvent(message="Conversation not found.", code="bad_request"))
            return
        user_msg = self.messages.add(conv.id, "user", event.text)
        assistant = self.messages.add(conv.id, "assistant", "", status="streaming")
        self._active[assistant.id] = asyncio.current_task()
        await emit(MessageStart(conversation_id=conv.id, message_id=assistant.id,
                                user_message_id=user_msg.id, client_id=event.client_id))
        if not conv.title:
            title = make_title(event.text)
            self.conversations.rename(conv.id, title)
            await emit(ConversationUpdated(conversation_id=conv.id, title=title))

        parts: list[str] = []
        status = "complete"
        try:
            async def on_switch(sw: ProviderSwitch) -> None:
                await emit(ProviderSwitched(role=sw.role, from_provider=sw.from_label,
                                            to_provider=sw.to_label, reason=sw.reason))

            async for ev in self.router.stream("chat", self._context(conv.id, assistant.id), on_switch=on_switch):
                if isinstance(ev, TextDelta):
                    parts.append(ev.text)
                    await emit(Token(message_id=assistant.id, text=ev.text))
        except asyncio.CancelledError:
            status = "stopped"
        except NoProviderAvailable as exc:
            status = "error"
            await emit(ErrorEvent(message=str(exc), code="no_provider", message_id=assistant.id))
        except ProviderError as exc:
            status = "error"
            await emit(ErrorEvent(message=str(exc), code="provider_error", message_id=assistant.id))
        except Exception:
            log.exception("chat turn failed")
            status = "error"
            await emit(ErrorEvent(message="Something went wrong while replying.", code="internal",
                                  message_id=assistant.id))
        finally:
            self.messages.update(assistant.id, content="".join(parts), status=status)
            self._active.pop(assistant.id, None)
            await emit(MessageEnd(message_id=assistant.id, status=status))

    def _context(self, conversation_id: str, exclude_id: str) -> list[ChatMessage]:
        window = self.settings.get().history_window
        history = [
            m for m in self.messages.list(conversation_id, limit=window + 1)
            if m.id != exclude_id and m.status != "error" and m.content
        ]
        return [ChatMessage("system", system_prompt())] + [ChatMessage(m.role, m.content) for m in history]
```

`backend/aethel/api/ws.py`:
```python
import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from .events import ErrorEvent, StopGeneration, UserMessage, client_event_adapter

router = APIRouter()


@router.websocket("/ws/session")
async def session_socket(websocket: WebSocket) -> None:
    services = websocket.app.state.services
    if not services.auth.check(websocket.query_params.get("token")):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    send_lock = asyncio.Lock()
    state = {"open": True}

    async def emit(event: BaseModel) -> None:
        # Generation keeps going (and persists) even if the socket went away.
        if not state["open"]:
            return
        async with send_lock:
            try:
                await websocket.send_text(event.model_dump_json())
            except Exception:
                state["open"] = False

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                event = client_event_adapter.validate_json(raw)
            except ValidationError:
                await emit(ErrorEvent(message="Invalid event.", code="bad_request"))
                continue
            if isinstance(event, UserMessage):
                services.chat.start_turn(event, emit)
            elif isinstance(event, StopGeneration):
                services.chat.stop(event.message_id)
    except WebSocketDisconnect:
        state["open"] = False
```

`backend/aethel/services.py`: add `from .chat.service import ChatService`, add the field `chat: ChatService` to `Services` (after `provider_factory`), and in `build_services` construct the router first and then the chat service:
```python
    router = RoleRouter(settings=settings, keys=keys, local=local, factory=factory)
    conversations, messages = ConversationRepo(db), MessageRepo(db)
    return Services(
        db=db,
        settings=settings,
        keys=keys,
        auth=AuthConfig.from_env(),
        conversations=conversations,
        messages=messages,
        local_llm=local,
        router=router,
        provider_factory=factory,
        chat=ChatService(conversations=conversations, messages=messages, router=router, settings=settings),
    )
```

`backend/aethel/app.py`: add `from .api import ws` and `app.include_router(ws.router)` after the other routers. In the lifespan `finally`, wait for in-flight turns before closing:
```python
        finally:
            await svc.chat.wait_idle()
            svc.close()
```

- [ ] **Step 4: Continue to Task 10 Steps 1–3, then run both files**

Run (after Task 10 Step 3): `cd backend && py -3.11 -m pytest tests/test_chat_ws.py tests/test_rest.py -v`
Expected: all pass

- [ ] **Step 5: Commit (together with Task 10, see Task 10 Step 5)**

---

### Task 10: REST routes for conversations, settings and providers

**Files:**
- Create: `backend/aethel/api/routes/conversations.py`, `backend/aethel/api/routes/settings.py`, `backend/aethel/api/routes/providers.py`
- Modify: `backend/aethel/app.py` (include the three routers)
- Test: `backend/tests/test_rest.py`

**Interfaces:**
- Consumes: `Services` (repos, settings, keys, local_llm, provider_factory), `PROVIDERS`, `RouteEntry`, `ChatMessage`, `TextDelta`, `ProviderError`
- Produces (all require auth):
  - `GET /api/conversations` returns `Conversation[]`
  - `POST /api/conversations` takes `{persona_id?}` and returns `Conversation`
  - `PATCH /api/conversations/{id}` takes `{title}` and returns `Conversation`
  - `DELETE /api/conversations/{id}` returns 204
  - `GET /api/conversations/{id}/messages` returns `Message[]`
  - `GET /api/settings` returns `AppSettings`
  - `PATCH /api/settings` takes a partial dict and returns `AppSettings` (422 on invalid)
  - `GET /api/providers` returns `[{id,label,needs_key,has_key,base_url}]`
  - `GET /api/providers/{id}/models` returns `{"models": string[]}` (400 if no key, 502 on error)
  - `POST /api/providers/test` takes `{provider, model}` and returns `{ok, latency_ms, reply, error}`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_rest.py`:
```python
from fastapi.testclient import TestClient

from aethel.app import create_app
from aethel.providers.base import ProviderError
from aethel.services import build_services
from tests.fakes import FakeLocal, FakeProvider, factory_from


def _client(mapping=None, local=None):
    services = build_services(provider_factory=factory_from(mapping or {}), local_llm=local or FakeLocal(up=False))
    return TestClient(create_app(services)), services


def test_conversation_crud():
    client, svc = _client()
    with client:
        a = client.post("/api/conversations", json={}).json()
        b = client.post("/api/conversations", json={"persona_id": "mira"}).json()
        assert b["persona_id"] == "mira"
        assert [c["id"] for c in client.get("/api/conversations").json()] == [b["id"], a["id"]]
        renamed = client.patch(f"/api/conversations/{a['id']}", json={"title": "Trip plans"})
        assert renamed.json()["title"] == "Trip plans"
        svc.messages.add(a["id"], "user", "hello")
        msgs = client.get(f"/api/conversations/{a['id']}/messages").json()
        assert [(m["role"], m["content"]) for m in msgs] == [("user", "hello")]
        assert client.delete(f"/api/conversations/{a['id']}").status_code == 204
        assert client.delete(f"/api/conversations/{a['id']}").status_code == 404
        assert client.get(f"/api/conversations/{a['id']}/messages").status_code == 404
        assert client.patch("/api/conversations/missing", json={"title": "x"}).status_code == 404


def test_settings_get_and_patch():
    client, _ = _client()
    with client:
        assert client.get("/api/settings").json()["private_mode"] is False
        res = client.patch("/api/settings", json={"private_mode": True, "local_llm": {"threads": 8}})
        assert res.status_code == 200
        body = client.get("/api/settings").json()
        assert body["private_mode"] is True and body["local_llm"]["threads"] == 8
        bad = client.patch("/api/settings", json={"roles": {"chat": [{"provider": "nope", "model": "x"}]}})
        assert bad.status_code == 422


def test_provider_list_reports_keys_but_not_values():
    client, svc = _client()
    svc.keys.set_many({"groq": "secret"})
    with client:
        providers = {p["id"]: p for p in client.get("/api/providers").json()}
    assert providers["groq"]["has_key"] is True and "secret" not in str(providers)
    assert providers["gemini"]["has_key"] is False
    assert providers["local"]["needs_key"] is False


def test_models_endpoint():
    client, svc = _client({"groq:_list": FakeProvider(models=["b-model", "a-model"])})
    with client:
        assert client.get("/api/providers/groq/models").status_code == 400
        svc.keys.set_many({"groq": "k"})
        assert client.get("/api/providers/groq/models").json() == {"models": ["b-model", "a-model"]}
        assert client.get("/api/providers/evil/models").status_code == 404


def test_local_models_empty_when_server_down():
    client, _ = _client(local=FakeLocal(up=False))
    with client:
        assert client.get("/api/providers/local/models").json() == {"models": []}


def test_provider_test_endpoint():
    ok = FakeProvider(chunks=["ok"])
    broken = FakeProvider(error=ProviderError("groq:bad: HTTP 404: model not found", retryable=False, status=404))
    client, svc = _client({"groq:good": ok, "groq:bad": broken})
    with client:
        no_key = client.post("/api/providers/test", json={"provider": "groq", "model": "good"}).json()
        assert no_key["ok"] is False and "API key" in no_key["error"]
        svc.keys.set_many({"groq": "k"})
        good = client.post("/api/providers/test", json={"provider": "groq", "model": "good"}).json()
        assert good["ok"] is True and good["reply"] == "ok" and good["latency_ms"] >= 0
        bad = client.post("/api/providers/test", json={"provider": "groq", "model": "bad"}).json()
        assert bad["ok"] is False and "model not found" in bad["error"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && py -3.11 -m pytest tests/test_rest.py -v`
Expected: FAIL (404 on `/api/conversations`)

- [ ] **Step 3: Implement the routes**

`backend/aethel/api/routes/conversations.py`:
```python
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from ...services import Services
from ...store.repos import Conversation, Message
from ..deps import get_services, require_auth

router = APIRouter(prefix="/api/conversations", dependencies=[Depends(require_auth)])


class CreateIn(BaseModel):
    persona_id: str = "aethel"


class RenameIn(BaseModel):
    title: str = Field(max_length=200)


def _require(svc: Services, conv_id: str) -> Conversation:
    conv = svc.conversations.get(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.get("")
def list_conversations(svc: Services = Depends(get_services)) -> list[Conversation]:
    return svc.conversations.list()


@router.post("")
def create_conversation(body: CreateIn, svc: Services = Depends(get_services)) -> Conversation:
    return svc.conversations.create(persona_id=body.persona_id)


@router.patch("/{conv_id}")
def rename_conversation(conv_id: str, body: RenameIn, svc: Services = Depends(get_services)) -> Conversation:
    _require(svc, conv_id)
    return svc.conversations.rename(conv_id, body.title.strip())


@router.delete("/{conv_id}", status_code=204)
def delete_conversation(conv_id: str, svc: Services = Depends(get_services)) -> Response:
    if not svc.conversations.delete(conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=204)


@router.get("/{conv_id}/messages")
def list_messages(conv_id: str, svc: Services = Depends(get_services)) -> list[Message]:
    _require(svc, conv_id)
    return svc.messages.list(conv_id)
```

`backend/aethel/api/routes/settings.py`:
```python
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError

from ...services import Services
from ...settings import AppSettings
from ..deps import get_services, require_auth

router = APIRouter(prefix="/api/settings", dependencies=[Depends(require_auth)])


@router.get("")
def get_settings(svc: Services = Depends(get_services)) -> AppSettings:
    return svc.settings.get()


@router.patch("")
def patch_settings(patch: dict = Body(...), svc: Services = Depends(get_services)) -> AppSettings:
    try:
        return svc.settings.update(patch)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc
```

`backend/aethel/api/routes/providers.py`:
```python
import time

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...providers.base import ChatMessage, ProviderError, TextDelta
from ...providers.catalog import PROVIDERS, base_url_for
from ...providers.router import LOCAL_API_KEY
from ...services import Services
from ...settings import ProviderId, RouteEntry
from ..deps import get_services, require_auth

router = APIRouter(prefix="/api/providers", dependencies=[Depends(require_auth)])


class ProviderInfo(BaseModel):
    id: str
    label: str
    needs_key: bool
    has_key: bool
    base_url: str


class TestIn(BaseModel):
    provider: ProviderId
    model: str


class TestOut(BaseModel):
    ok: bool
    latency_ms: int
    reply: str | None = None
    error: str | None = None


@router.get("")
def list_providers(svc: Services = Depends(get_services)) -> list[ProviderInfo]:
    s = svc.settings.get()
    return [
        ProviderInfo(id=m.id, label=m.label, needs_key=m.needs_key,
                     has_key=(not m.needs_key) or svc.keys.get(m.id) is not None,
                     base_url=base_url_for(m.id, s))
        for m in PROVIDERS.values()
    ]


@router.get("/{provider_id}/models")
async def list_models(provider_id: str, svc: Services = Depends(get_services)) -> dict:
    if provider_id not in PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    if provider_id == "local":
        if svc.local_llm is None or not await anyio.to_thread.run_sync(svc.local_llm.is_up):
            return {"models": []}
        api_key = LOCAL_API_KEY
    else:
        api_key = svc.keys.get(provider_id)
        if not api_key:
            raise HTTPException(status_code=400, detail="No API key saved for this provider.")
    lister = svc.provider_factory(RouteEntry(provider=provider_id, model="_list"), api_key, svc.settings.get())
    try:
        return {"models": await lister.list_models()}
    except (ProviderError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/test")
async def test_provider(body: TestIn, svc: Services = Depends(get_services)) -> TestOut:
    if body.provider == "local":
        try:
            await anyio.to_thread.run_sync(svc.local_llm.ensure_running)
        except Exception as exc:
            return TestOut(ok=False, latency_ms=0, error=str(exc))
        api_key = LOCAL_API_KEY
    else:
        api_key = svc.keys.get(body.provider)
        if not api_key:
            return TestOut(ok=False, latency_ms=0, error="No API key saved for this provider.")
    provider = svc.provider_factory(RouteEntry(provider=body.provider, model=body.model), api_key,
                                    svc.settings.get())
    started = time.perf_counter()
    parts: list[str] = []
    try:
        async for ev in provider.stream([ChatMessage("user", "Reply with the single word: ok")],
                                        temperature=0.0, max_tokens=16):
            if isinstance(ev, TextDelta):
                parts.append(ev.text)
    except ProviderError as exc:
        return TestOut(ok=False, latency_ms=int((time.perf_counter() - started) * 1000), error=str(exc))
    return TestOut(ok=True, latency_ms=int((time.perf_counter() - started) * 1000), reply="".join(parts).strip())
```

`backend/aethel/app.py`: change the routes import to `from .api.routes import conversations, health, keys, providers, settings` and include `conversations.router`, `settings.router` and `providers.router` next to the existing ones.

- [ ] **Step 4: Run the whole suite to verify it passes**

Run: `cd backend && py -3.11 -m pytest -v`
Expected: all tests pass (including `test_chat_ws.py` from Task 9)

- [ ] **Step 5: Commit Tasks 9 and 10 together**

```bash
git add backend/aethel backend/tests
git commit -m "feat(backend): add streaming chat over /ws/session and REST for conversations, settings, providers" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Tauri shell that launches the backend and stores keys

**Files:**
- Modify: `frontend_app/src-tauri/Cargo.toml`, `frontend_app/src-tauri/src/lib.rs` (full replacement), `frontend_app/src-tauri/tauri.conf.json`, `frontend_app/src-tauri/capabilities/default.json`

**Interfaces:**
- Produces Tauri commands for the frontend:
  - `get_backend_info() -> { url: string, token: string }`
  - `secret_set(provider: string, value: string) -> void`
  - `secret_delete(provider: string) -> void`
  - `secret_get_all() -> Record<string, string>`

  Providers are limited to `groq | gemini | openrouter | custom | typesafe`.
- Spawns the backend (`<python> -m aethel`, cwd `<repo>/backend`) with the env vars `AETHEL_TOKEN`, `AETHEL_PORT` and `AETHEL_PARENT_PID`. Logs go to `~/.aethel/logs/backend.log`. The backend is killed on exit. The env var `AETHEL_EXTERNAL_BACKEND=1` skips spawning.

- [ ] **Step 1: Write the failing Rust unit tests**

Append to the new `lib.rs` (the full file is in Step 3; the tests live at its bottom):
```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_known_providers_are_accepted() {
        assert!(validate_provider("groq").is_ok());
        assert!(validate_provider("typesafe").is_ok());
        assert!(validate_provider("../evil").is_err());
    }

    #[test]
    fn aethel_home_honours_env() {
        std::env::set_var("AETHEL_HOME", "C:\\tmp\\aethel-test");
        assert_eq!(aethel_home(), std::path::PathBuf::from("C:\\tmp\\aethel-test"));
        std::env::remove_var("AETHEL_HOME");
    }
}
```

- [ ] **Step 2: Update Cargo.toml**

In `[dependencies]`: remove `tauri-plugin-fs` and `tauri-plugin-http`, and add:
```toml
keyring = { version = "3", features = ["windows-native"] }
uuid = { version = "1", features = ["v4"] }
```

- [ ] **Step 3: Replace `src/lib.rs`**

```rust
use std::collections::HashMap;
use std::fs::{self, File};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::Serialize;
use tauri::{Manager, RunEvent};

const KEYRING_SERVICE: &str = "com.aethel.app";
const KNOWN_PROVIDERS: [&str; 5] = ["groq", "gemini", "openrouter", "custom", "typesafe"];

#[derive(Clone, Serialize)]
struct BackendInfo {
    url: String,
    token: String,
}

struct BackendProcess(Mutex<Option<Child>>);

fn validate_provider(provider: &str) -> Result<(), String> {
    if KNOWN_PROVIDERS.contains(&provider) {
        Ok(())
    } else {
        Err(format!("unknown provider: {provider}"))
    }
}

fn aethel_home() -> PathBuf {
    if let Ok(home) = std::env::var("AETHEL_HOME") {
        return PathBuf::from(home);
    }
    let base = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .unwrap_or_else(|_| ".".into());
    PathBuf::from(base).join(".aethel")
}

fn repo_root() -> PathBuf {
    // Dev layout: <repo>/frontend_app/src-tauri
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// Resolve the real interpreter path. Killing the `py` launcher does not kill
/// the python.exe it starts, so we must spawn python.exe directly.
fn python_executable(repo: &Path) -> String {
    if let Ok(p) = std::env::var("AETHEL_PYTHON") {
        return p;
    }
    let venv = repo.join(".venv").join("Scripts").join("python.exe");
    if venv.exists() {
        return venv.to_string_lossy().into_owned();
    }
    if cfg!(windows) {
        if let Ok(out) = Command::new("py")
            .args(["-3.11", "-c", "import sys; print(sys.executable)"])
            .output()
        {
            let path = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if !path.is_empty() {
                return path;
            }
        }
        return "python".into();
    }
    "python3".into()
}

fn spawn_backend(token: &str, port: u16) -> std::io::Result<Child> {
    let repo = repo_root();
    let backend_dir = std::env::var("AETHEL_BACKEND_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| repo.join("backend"));
    let logs = aethel_home().join("logs");
    fs::create_dir_all(&logs)?;
    let log = File::create(logs.join("backend.log"))?;

    let mut cmd = Command::new(python_executable(&repo));
    cmd.args(["-m", "aethel"])
        .current_dir(backend_dir)
        .env("AETHEL_TOKEN", token)
        .env("AETHEL_PORT", port.to_string())
        .env("AETHEL_PARENT_PID", std::process::id().to_string())
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    cmd.spawn()
}

#[tauri::command]
fn get_backend_info(info: tauri::State<BackendInfo>) -> BackendInfo {
    info.inner().clone()
}

#[tauri::command]
fn secret_set(provider: String, value: String) -> Result<(), String> {
    validate_provider(&provider)?;
    keyring::Entry::new(KEYRING_SERVICE, &provider)
        .and_then(|e| e.set_password(&value))
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn secret_delete(provider: String) -> Result<(), String> {
    validate_provider(&provider)?;
    match keyring::Entry::new(KEYRING_SERVICE, &provider).and_then(|e| e.delete_credential()) {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(e.to_string()),
    }
}

#[tauri::command]
fn secret_get_all() -> Result<HashMap<String, String>, String> {
    let mut out = HashMap::new();
    for provider in KNOWN_PROVIDERS {
        let entry = keyring::Entry::new(KEYRING_SERVICE, provider).map_err(|e| e.to_string())?;
        match entry.get_password() {
            Ok(value) => {
                out.insert(provider.to_string(), value);
            }
            Err(keyring::Error::NoEntry) => {}
            Err(e) => return Err(e.to_string()),
        }
    }
    Ok(out)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let port: u16 = std::env::var("AETHEL_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(8765);
    let token = std::env::var("AETHEL_TOKEN").unwrap_or_else(|_| uuid::Uuid::new_v4().simple().to_string());
    let info = BackendInfo { url: format!("http://127.0.0.1:{port}"), token: token.clone() };

    let app = tauri::Builder::default()
        .manage(info)
        .manage(BackendProcess(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![get_backend_info, secret_set, secret_delete, secret_get_all])
        .setup(move |app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default().level(log::LevelFilter::Info).build(),
                )?;
            }
            if std::env::var("AETHEL_EXTERNAL_BACKEND").as_deref() != Ok("1") {
                match spawn_backend(&token, port) {
                    Ok(child) => *app.state::<BackendProcess>().0.lock().unwrap() = Some(child),
                    Err(e) => log::error!("failed to start the Aethel backend: {e}"),
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Aethel");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(mut child) = handle.state::<BackendProcess>().0.lock().unwrap().take() {
                let _ = child.kill();
            }
        }
    });
}

// (tests module from Step 1 goes here)
```

- [ ] **Step 4: Update the config and capabilities**

`tauri.conf.json`: set `app.windows[0]` to
```json
{ "label": "main", "title": "Aethel", "width": 1280, "height": 820, "minWidth": 960, "minHeight": 640, "decorations": true, "resizable": true }
```
and set `app.security.csp` to:
```
default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: http://127.0.0.1:8765; font-src 'self' data:; connect-src 'self' ipc: http://ipc.localhost http://127.0.0.1:8765 ws://127.0.0.1:8765
```

`capabilities/default.json`: set `"permissions": ["core:default"]` (remove the `http:` and `fs:` entries, since those plugins are gone).

- [ ] **Step 5: Build and run the Rust tests**

Run: `cd frontend_app/src-tauri && cargo test`
Expected: compiles; `2 passed`. (The first build downloads crates and can take several minutes.)

- [ ] **Step 6: Commit**

```bash
git add frontend_app/src-tauri/Cargo.toml frontend_app/src-tauri/Cargo.lock frontend_app/src-tauri/src/lib.rs frontend_app/src-tauri/tauri.conf.json frontend_app/src-tauri/capabilities/default.json
git commit -m "feat(shell): launch backend from Tauri, pass auth token, store API keys in Credential Manager" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Frontend reset: dependencies, Paper & Ink tokens, fonts, test setup

**Files:**
- Delete: `frontend_app/src/app/` (whole folder), `frontend_app/src/styles/fonts.css`, `globals.css`, `tailwind.css`, `theme.css`, `frontend_app/default_shadcn_theme.css`, `frontend_app/canonical_face_model.obj`, `frontend_app/ATTRIBUTIONS.md`, `frontend_app/postcss.config.mjs`
- Modify: `frontend_app/package.json`, `frontend_app/vite.config.ts` (full replacement), `frontend_app/index.html`, `frontend_app/tsconfig.json` (add `"types": ["vitest/globals"]`)
- Create: `frontend_app/src/styles/index.css`, `tokens.css`, `base.css`, `frontend_app/src/fonts.ts`, `frontend_app/src/vite-env.d.ts`, `frontend_app/src/main.tsx` (replacement), `frontend_app/src/App.tsx`, `frontend_app/src/test/setup.ts`
- Test: `frontend_app/src/App.test.tsx`

**Interfaces:**
- Produces:
  - Tailwind utilities `bg-canvas`, `bg-paper`, `bg-well`, `text-ink`, `text-ink-2`, `text-muted`, `text-faint`, `border-hairline`, `bg-accent`, `text-accent`, `bg-accent-soft`, `font-display`, `font-voice` and `font-sans`
  - CSS classes `.grain` and `.ink-word`
  - CSS vars `--dur-fast`, `--dur-base`, `--dur-slow` and `--ease-out`
  - `<html data-theme="light|dark" data-motion="full|reduced">` drives the theme and motion

- [ ] **Step 1: Swap the dependencies**

Run in `frontend_app/`:
```bash
pnpm remove @emotion/react @emotion/styled @radix-ui/react-accordion @radix-ui/react-alert-dialog @radix-ui/react-aspect-ratio @radix-ui/react-avatar @radix-ui/react-checkbox @radix-ui/react-collapsible @radix-ui/react-context-menu @radix-ui/react-dropdown-menu @radix-ui/react-hover-card @radix-ui/react-label @radix-ui/react-menubar @radix-ui/react-navigation-menu @radix-ui/react-popover @radix-ui/react-progress @radix-ui/react-radio-group @radix-ui/react-scroll-area @radix-ui/react-select @radix-ui/react-separator @radix-ui/react-slider @radix-ui/react-slot @radix-ui/react-tabs @radix-ui/react-toggle @radix-ui/react-toggle-group @tauri-apps/plugin-fs @tauri-apps/plugin-http class-variance-authority cmdk date-fns embla-carousel-react input-otp next-themes react-day-picker react-hook-form react-resizable-panels recharts tw-animate-css vaul
pnpm add react@18.3.1 react-dom@18.3.1 zustand @tanstack/react-query @fontsource-variable/inter @fontsource/instrument-serif @fontsource-variable/newsreader
pnpm add -D vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event json-schema-to-typescript
```
Then edit `package.json`: set `"name": "aethel"`, delete the `peerDependencies` and `peerDependenciesMeta` blocks, and set `scripts` to:
```json
{
  "dev": "vite",
  "build": "tsc --noEmit && vite build",
  "test": "vitest run",
  "gen:types": "json2ts -i src/lib/events.schema.json -o src/lib/events.gen.ts --bannerComment \"/* Generated from backend/aethel/api/events.py by pnpm gen:types. Do not edit. */\"",
  "tauri": "tauri"
}
```
Remaining runtime deps: `react`, `react-dom`, `@tauri-apps/api`, `@radix-ui/react-dialog`, `@radix-ui/react-switch`, `@radix-ui/react-tooltip`, `clsx`, `tailwind-merge`, `lucide-react`, `motion`, `react-markdown`, `sonner`, `zustand`, `@tanstack/react-query`, and the three fontsource packages.

- [ ] **Step 2: Delete the legacy UI**

```bash
git rm -r -q frontend_app/src/app frontend_app/src/styles/fonts.css frontend_app/src/styles/globals.css frontend_app/src/styles/tailwind.css frontend_app/src/styles/theme.css frontend_app/default_shadcn_theme.css frontend_app/canonical_face_model.obj frontend_app/ATTRIBUTIONS.md frontend_app/postcss.config.mjs
```

- [ ] **Step 3: Write the smoke test and the test setup**

`frontend_app/src/test/setup.ts`:
```ts
import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => cleanup());

if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}
Element.prototype.scrollTo = Element.prototype.scrollTo || function () {};
```

`frontend_app/src/App.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import { App } from "./App";

test("renders the Aethel wordmark", () => {
  render(<App />);
  expect(screen.getByText("Aethel")).toBeInTheDocument();
});
```

- [ ] **Step 4: Write the Vite config, index.html, styles, fonts, entry and placeholder App**

`frontend_app/vite.config.ts`:
```ts
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const root = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(root, "./src") } },
  clearScreen: false,
  server: { port: 5173, strictPort: true, watch: { ignored: ["**/src-tauri/**"] } },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.ts"], css: false },
});
```

`frontend_app/index.html`:
```html
<!doctype html>
<html lang="en" data-theme="light" data-motion="full">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Aethel</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`frontend_app/src/styles/tokens.css`:
```css
:root,
[data-theme="light"] {
  --canvas: #f3efe7;
  --paper: #fbf9f4;
  --well: #ece6da;
  --ink: #1d1b17;
  --ink-2: #2a2620;
  --muted: #7a746a;
  --faint: #a39b8e;
  --hairline: #e2dccf;
  --accent: #c2553a;
  --accent-soft: color-mix(in oklab, var(--accent) 12%, transparent);
  --grain-opacity: 0.06;
  --grain-blend: multiply;
  --lift: 0 18px 40px -18px rgb(90 50 30 / 0.45);
  --dur-fast: 160ms;
  --dur-base: 280ms;
  --dur-slow: 450ms;
  --ease-out: cubic-bezier(0.22, 1, 0.36, 1);
  color-scheme: light;
}

[data-theme="dark"] {
  --canvas: #1a1815;
  --paper: #211e1a;
  --well: #2e2a24;
  --ink: #ece6da;
  --ink-2: #e4ddcf;
  --muted: #9a9285;
  --faint: #7d766b;
  --hairline: #2c2823;
  --accent: #e07a5f;
  --grain-opacity: 0.05;
  --grain-blend: screen;
  --lift: 0 18px 40px -18px rgb(0 0 0 / 0.6);
  color-scheme: dark;
}

[data-motion="reduced"] {
  --dur-fast: 0ms;
  --dur-base: 0ms;
  --dur-slow: 0ms;
}
```

`frontend_app/src/styles/base.css`:
```css
html,
body,
#root {
  height: 100%;
}

body {
  margin: 0;
  background: var(--canvas);
  color: var(--ink);
  font-family: "Inter Variable", ui-sans-serif, system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}

::selection {
  background: var(--accent-soft);
}

* {
  scrollbar-width: thin;
  scrollbar-color: var(--hairline) transparent;
}

/* Paper grain: an SVG noise overlay above the surface. */
.grain {
  position: relative;
  isolation: isolate;
}
.grain::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 50;
  opacity: var(--grain-opacity);
  mix-blend-mode: var(--grain-blend);
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
}

/* Ink settling: new words arrive faint and soft, then sharpen (spec §12.4). */
@keyframes ink-settle {
  from {
    opacity: 0.35;
    filter: blur(1.2px);
  }
  to {
    opacity: 1;
    filter: blur(0);
  }
}
.ink-word {
  animation: ink-settle 350ms var(--ease-out) both;
}
[data-motion="reduced"] .ink-word {
  animation: none;
}

@keyframes breathe {
  0%,
  100% {
    opacity: 0.45;
    transform: scale(1);
  }
  50% {
    opacity: 1;
    transform: scale(1.15);
  }
}
.breathe {
  animation: breathe 2.4s ease-in-out infinite;
}
[data-motion="reduced"] .breathe {
  animation: none;
}
```

`frontend_app/src/styles/index.css`:
```css
@import "tailwindcss";
@import "./tokens.css";

@custom-variant dark (&:where([data-theme="dark"], [data-theme="dark"] *));

@theme inline {
  --color-canvas: var(--canvas);
  --color-paper: var(--paper);
  --color-well: var(--well);
  --color-ink: var(--ink);
  --color-ink-2: var(--ink-2);
  --color-muted: var(--muted);
  --color-faint: var(--faint);
  --color-hairline: var(--hairline);
  --color-accent: var(--accent);
  --color-accent-soft: var(--accent-soft);
  --font-display: "Instrument Serif", ui-serif, Georgia, serif;
  --font-voice: "Newsreader Variable", ui-serif, Georgia, serif;
  --font-sans: "Inter Variable", ui-sans-serif, system-ui, sans-serif;
  --shadow-lift: var(--lift);
}

@import "./base.css";
```

`frontend_app/src/fonts.ts` (check the CSS file names in `node_modules/@fontsource-variable/newsreader` and `@fontsource/instrument-serif`; the ones below are the fontsource conventions):
```ts
import "@fontsource-variable/inter";
import "@fontsource/instrument-serif/400.css";
import "@fontsource/instrument-serif/400-italic.css";
import "@fontsource-variable/newsreader/opsz.css";
import "@fontsource-variable/newsreader/opsz-italic.css";
```

`frontend_app/src/vite-env.d.ts`:
```ts
/// <reference types="vite/client" />
interface ImportMetaEnv {
  readonly VITE_BACKEND_URL?: string;
  readonly VITE_AETHEL_TOKEN?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
```

`frontend_app/src/App.tsx` (placeholder, replaced in Task 15):
```tsx
export function App() {
  return (
    <div className="grain flex h-full items-center justify-center bg-canvas text-ink">
      <h1 className="font-display text-6xl">Aethel</h1>
    </div>
  );
}
```

`frontend_app/src/main.tsx`:
```tsx
import React from "react";
import { createRoot } from "react-dom/client";
import "./fonts";
import "./styles/index.css";
import { App } from "./App";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

`frontend_app/tsconfig.json`: add `"types": ["vitest/globals"]` inside `compilerOptions`.

- [ ] **Step 5: Run the test and the build**

Run: `cd frontend_app && pnpm test && pnpm build`
Expected: `1 passed`; `tsc` shows no errors; vite build succeeds.

- [ ] **Step 6: Commit**

```bash
git add -A frontend_app
git commit -m "feat(ui): reset frontend to Paper & Ink foundation with self-hosted fonts and vitest" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Frontend transport: backend info, REST client, WebSocket, keys, generated event types

**Files:**
- Create: `frontend_app/src/lib/backend.ts`, `api.ts`, `types.ts`, `ws.ts`, `session.ts`, `keys.ts`, `events.ts`, `ids.ts`, and the generated `events.gen.ts`
- Test: `frontend_app/src/lib/ws.test.ts`, `frontend_app/src/lib/api.test.ts`

**Interfaces:**
- Consumes: `events.schema.json` (Task 8); the Tauri commands (Task 11)
- Produces:
  - `backend.ts`: `inTauri()`, `getBackendInfo(): Promise<BackendInfo>` and `resetBackendInfo()`
  - `api.ts`: `api<T>(path, init?)`, `ApiError(status, message)` and `waitForBackend(timeoutMs)`
  - `types.ts`: `Conversation`, `Message`, `RouteEntry`, `LocalLLMSettings`, `AppSettings`, `ProviderInfo` and `ProviderTestResult`
  - `events.ts`: `ServerEvent` and `ClientEvent`
  - `ws.ts`: `SessionSocket` (`connect()`, `send(ev)`, `subscribe(fn)`, `onStatus(fn)`, `close()`), the `SessionTransport` interface, `SocketStatus` and `sessionUrl()`
  - `session.ts`: `getSocket(): SessionTransport` and `setSocketForTests(t)`
  - `keys.ts`: `pushStoredKeys()` and `saveKey(provider, value | null)`
  - `ids.ts`: `newClientId()`

- [ ] **Step 1: Generate the event types**

Run: `cd frontend_app && pnpm gen:types`
Expected: `src/lib/events.gen.ts` is created and contains `export interface AethelProtocol` plus interfaces `MessageStart`, `Token`, `UserMessage` and the rest.

`frontend_app/src/lib/events.ts`:
```ts
import type { AethelProtocol } from "./events.gen";

export type ServerEvent = AethelProtocol["server"];
export type ClientEvent = AethelProtocol["client"];
```

- [ ] **Step 2: Write the failing tests**

`frontend_app/src/lib/ws.test.ts`:
```ts
import { SessionSocket } from "./ws";

class FakeWS {
  static instances: FakeWS[] = [];
  static OPEN = 1;
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) {
    FakeWS.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.onclose?.();
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
}

beforeEach(() => {
  FakeWS.instances = [];
  vi.useFakeTimers();
});
afterEach(() => vi.useRealTimers());

const makeSocket = () =>
  new SessionSocket(async () => "ws://x/ws/session", FakeWS as unknown as typeof WebSocket);

test("queues sends until open, then flushes in order", async () => {
  const s = makeSocket();
  await s.connect();
  s.send({ type: "stop_generation", message_id: "a" });
  s.send({ type: "stop_generation", message_id: "b" });
  const ws = FakeWS.instances[0];
  expect(ws.sent).toEqual([]);
  ws.open();
  expect(ws.sent.map((d) => JSON.parse(d).message_id)).toEqual(["a", "b"]);
});

test("dispatches parsed server events and reports status", async () => {
  const s = makeSocket();
  const events: unknown[] = [];
  const statuses: string[] = [];
  s.subscribe((e) => events.push(e));
  s.onStatus((st) => statuses.push(st));
  await s.connect();
  const ws = FakeWS.instances[0];
  ws.open();
  ws.onmessage?.({ data: JSON.stringify({ type: "token", message_id: "m", text: "hi" }) });
  ws.onmessage?.({ data: "not json" });
  expect(events).toEqual([{ type: "token", message_id: "m", text: "hi" }]);
  expect(statuses).toEqual(["connecting", "open"]);
});

test("reconnects with backoff after an unexpected close, not after close()", async () => {
  const s = makeSocket();
  await s.connect();
  FakeWS.instances[0].open();
  FakeWS.instances[0].onclose?.();
  await vi.advanceTimersByTimeAsync(1000);
  expect(FakeWS.instances).toHaveLength(2);
  s.close();
  await vi.advanceTimersByTimeAsync(20000);
  expect(FakeWS.instances).toHaveLength(2);
});
```

`frontend_app/src/lib/api.test.ts`:
```ts
import { api, ApiError } from "./api";
import { resetBackendInfo } from "./backend";

beforeEach(() => resetBackendInfo());

test("sends JSON with the bearer token and parses the response", async () => {
  vi.stubEnv("VITE_AETHEL_TOKEN", "tok");
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: 1 }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  const out = await api<{ ok: number }>("/api/x", { method: "POST", body: JSON.stringify({ a: 1 }) });
  expect(out).toEqual({ ok: 1 });
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("http://127.0.0.1:8765/api/x");
  expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok");
  expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

test("throws ApiError with the backend detail", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "nope" }), { status: 404 })));
  await expect(api("/api/y")).rejects.toEqual(new ApiError(404, "nope"));
  vi.unstubAllGlobals();
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd frontend_app && pnpm test`
Expected: FAIL (cannot resolve `./ws` / `./api`)

- [ ] **Step 4: Implement the transport modules**

`frontend_app/src/lib/backend.ts`:
```ts
export interface BackendInfo {
  url: string;
  token: string | null;
}

export function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

let cached: Promise<BackendInfo> | null = null;

export function getBackendInfo(): Promise<BackendInfo> {
  if (!cached) {
    cached = (async () => {
      if (inTauri()) {
        const { invoke } = await import("@tauri-apps/api/core");
        return invoke<BackendInfo>("get_backend_info");
      }
      return {
        url: import.meta.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8765",
        token: import.meta.env.VITE_AETHEL_TOKEN ?? null,
      };
    })();
  }
  return cached;
}

export function resetBackendInfo() {
  cached = null;
}
```

`frontend_app/src/lib/api.ts`:
```ts
import { getBackendInfo } from "./backend";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { url, token } = await getBackendInfo();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const res = await fetch(`${url}${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Resolves once /api/health answers (the backend takes a few seconds to boot). */
export async function waitForBackend(timeoutMs = 60000, intervalMs = 500): Promise<void> {
  const { url } = await getBackendInfo();
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${url}/api/health`);
      if (res.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error("The Aethel backend did not start. Check ~/.aethel/logs/backend.log.");
}
```

`frontend_app/src/lib/types.ts`:
```ts
export type ProviderId = "groq" | "gemini" | "openrouter" | "custom" | "local";
export type MessageStatus = "complete" | "streaming" | "stopped" | "error";

export interface Conversation {
  id: string;
  title: string;
  persona_id: string;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  status: MessageStatus;
  meta: Record<string, unknown>;
  created_at: string;
}

export interface RouteEntry {
  provider: ProviderId;
  model: string;
}

export interface LocalLLMSettings {
  model_path: string;
  context_size: number;
  threads: number;
  gpu_layers: number;
}

export interface AppSettings {
  roles: Record<string, RouteEntry[]>;
  custom_base_url: string;
  private_mode: boolean;
  internet: boolean;
  local_llm: LocalLLMSettings;
  temperature: number;
  max_tokens: number;
  history_window: number;
}

export interface ProviderInfo {
  id: ProviderId;
  label: string;
  needs_key: boolean;
  has_key: boolean;
  base_url: string;
}

export interface ProviderTestResult {
  ok: boolean;
  latency_ms: number;
  reply: string | null;
  error: string | null;
}
```

`frontend_app/src/lib/ids.ts`:
```ts
export function newClientId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `c_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}
```

`frontend_app/src/lib/ws.ts`:
```ts
import { getBackendInfo } from "./backend";
import type { ClientEvent, ServerEvent } from "./events";

export type SocketStatus = "connecting" | "open" | "closed";

export interface SessionTransport {
  send(event: ClientEvent): void;
  subscribe(listener: (event: ServerEvent) => void): () => void;
  onStatus(listener: (status: SocketStatus) => void): () => void;
}

export async function sessionUrl(): Promise<string> {
  const { url, token } = await getBackendInfo();
  const ws = new URL("/ws/session", url.replace(/^http/, "ws"));
  if (token) ws.searchParams.set("token", token);
  return ws.toString();
}

export class SessionSocket implements SessionTransport {
  private ws: WebSocket | null = null;
  private listeners = new Set<(event: ServerEvent) => void>();
  private statusListeners = new Set<(status: SocketStatus) => void>();
  private queue: ClientEvent[] = [];
  private retries = 0;
  private stopped = false;

  constructor(
    private makeUrl: () => Promise<string> = sessionUrl,
    private WS: typeof WebSocket = WebSocket,
  ) {}

  async connect(): Promise<void> {
    this.stopped = false;
    this.setStatus("connecting");
    const ws = new this.WS(await this.makeUrl());
    this.ws = ws;
    ws.onopen = () => {
      this.retries = 0;
      this.setStatus("open");
      const pending = this.queue.splice(0);
      pending.forEach((ev) => ws.send(JSON.stringify(ev)));
    };
    ws.onmessage = (msg: MessageEvent | { data: string }) => {
      let event: ServerEvent;
      try {
        event = JSON.parse(String(msg.data)) as ServerEvent;
      } catch {
        return;
      }
      this.listeners.forEach((l) => l(event));
    };
    ws.onclose = () => {
      this.ws = null;
      this.setStatus("closed");
      if (this.stopped) return;
      const delay = Math.min(1000 * 2 ** this.retries, 10000);
      this.retries += 1;
      setTimeout(() => {
        if (!this.stopped) void this.connect();
      }, delay);
    };
  }

  send(event: ClientEvent): void {
    if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(event));
    else this.queue.push(event);
  }

  subscribe(listener: (event: ServerEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onStatus(listener: (status: SocketStatus) => void): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  close(): void {
    this.stopped = true;
    this.ws?.close();
  }

  private setStatus(status: SocketStatus) {
    this.statusListeners.forEach((l) => l(status));
  }
}
```

`frontend_app/src/lib/session.ts`:
```ts
import { SessionSocket, type SessionTransport } from "./ws";

let transport: SessionTransport | null = null;

export function getSocket(): SessionTransport {
  if (!transport) {
    const socket = new SessionSocket();
    void socket.connect();
    transport = socket;
  }
  return transport;
}

export function setSocketForTests(t: SessionTransport | null) {
  transport = t;
}
```

`frontend_app/src/lib/keys.ts`:
```ts
import { api } from "./api";
import { inTauri } from "./backend";

export type KeyProvider = "groq" | "gemini" | "openrouter" | "custom";

/** On startup: copy keys from Windows Credential Manager into backend memory. */
export async function pushStoredKeys(): Promise<void> {
  if (!inTauri()) return; // browser dev: use env vars (GROQ_API_KEY etc.) on the backend
  const { invoke } = await import("@tauri-apps/api/core");
  const all = await invoke<Record<string, string>>("secret_get_all");
  const keys = Object.fromEntries(Object.entries(all).filter(([p]) => p !== "typesafe"));
  if (Object.keys(keys).length) {
    await api("/api/keys", { method: "POST", body: JSON.stringify({ keys }) });
  }
}

/** Save (or with null, remove) a key: OS credential store + backend memory. */
export async function saveKey(provider: KeyProvider, value: string | null): Promise<void> {
  if (inTauri()) {
    const { invoke } = await import("@tauri-apps/api/core");
    if (value) await invoke("secret_set", { provider, value });
    else await invoke("secret_delete", { provider });
  }
  await api("/api/keys", { method: "POST", body: JSON.stringify({ keys: { [provider]: value } }) });
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd frontend_app && pnpm test`
Expected: all pass (App smoke + 3 ws + 2 api)

- [ ] **Step 6: Commit**

```bash
git add frontend_app/src/lib
git commit -m "feat(ui): add typed backend transport (REST, reconnecting websocket, keychain keys)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Session store (Zustand) with a pure event reducer

**Files:**
- Create: `frontend_app/src/stores/session.ts`
- Test: `frontend_app/src/stores/session.test.ts`

**Interfaces:**
- Consumes: `ServerEvent` (Task 13), `Message` (types)
- Produces:
  - `UiMessage { id; role: "user"|"assistant"; content; status: MessageStatus | "pending"; error?: string; errorCode?: string; clientId?: string }`
  - `SessionData { conversationId: string | null; messages: UiMessage[]; streamingId: string | null; notices: Notice[]; socketStatus: SocketStatus }`
  - `Notice { id: string; text: string }`
  - `applyEvent(data: SessionData, ev: ServerEvent): SessionData` (pure)
  - `toUiMessages(messages: Message[]): UiMessage[]`
  - `useSession`, a Zustand hook with actions `setConversation(id, messages)`, `addPendingUser(text, clientId)`, `apply(ev)`, `setSocketStatus(s)` and `dismissNotice(id)`

- [ ] **Step 1: Write the failing tests**

`frontend_app/src/stores/session.test.ts`:
```ts
import { applyEvent, toUiMessages, type SessionData } from "./session";

const base = (): SessionData => ({
  conversationId: "c1",
  messages: [{ id: "tmp", role: "user", content: "hi", status: "pending", clientId: "k1" }],
  streamingId: null,
  notices: [],
  socketStatus: "open",
});

test("message_start confirms the pending user message and opens an assistant bubble", () => {
  const next = applyEvent(base(), {
    type: "message_start", conversation_id: "c1", message_id: "a1", user_message_id: "u1", client_id: "k1", role: "assistant",
  });
  expect(next.messages).toEqual([
    { id: "u1", role: "user", content: "hi", status: "complete", clientId: "k1" },
    { id: "a1", role: "assistant", content: "", status: "streaming" },
  ]);
  expect(next.streamingId).toBe("a1");
});

test("tokens append, message_end settles", () => {
  let s = applyEvent(base(), {
    type: "message_start", conversation_id: "c1", message_id: "a1", user_message_id: "u1", client_id: "k1", role: "assistant",
  });
  s = applyEvent(s, { type: "token", message_id: "a1", text: "Hel" });
  s = applyEvent(s, { type: "token", message_id: "a1", text: "lo" });
  s = applyEvent(s, { type: "message_end", message_id: "a1", status: "complete" });
  expect(s.messages[1]).toMatchObject({ content: "Hello", status: "complete" });
  expect(s.streamingId).toBeNull();
});

test("events for another conversation are ignored", () => {
  const s = applyEvent(base(), {
    type: "message_start", conversation_id: "other", message_id: "a1", user_message_id: "u1", client_id: null, role: "assistant",
  });
  expect(s).toEqual(base());
  expect(applyEvent(base(), { type: "token", message_id: "zzz", text: "x" })).toEqual(base());
});

test("errors attach to their message or become notices", () => {
  let s = applyEvent(base(), {
    type: "message_start", conversation_id: "c1", message_id: "a1", user_message_id: "u1", client_id: "k1", role: "assistant",
  });
  s = applyEvent(s, { type: "error", code: "no_provider", message: "groq:x: no API key", message_id: "a1" });
  expect(s.messages[1]).toMatchObject({ status: "error", error: "groq:x: no API key", errorCode: "no_provider" });
  s = applyEvent(s, { type: "error", code: "bad_request", message: "Invalid event.", message_id: null });
  expect(s.notices.map((n) => n.text)).toEqual(["Invalid event."]);
});

test("provider switches become gentle notices", () => {
  const s = applyEvent(base(), {
    type: "provider_switched", role: "chat", from_provider: "groq:a", to_provider: "openrouter:b", reason: "429",
  });
  expect(s.notices[0].text).toBe("groq:a was unavailable, so openrouter:b answered instead.");
});

test("toUiMessages drops system messages", () => {
  expect(
    toUiMessages([
      { id: "s", conversation_id: "c", role: "system", content: "x", status: "complete", meta: {}, created_at: "" },
      { id: "u", conversation_id: "c", role: "user", content: "hi", status: "complete", meta: {}, created_at: "" },
    ]),
  ).toEqual([{ id: "u", role: "user", content: "hi", status: "complete" }]);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend_app && pnpm test src/stores`
Expected: FAIL (cannot resolve `./session`)

- [ ] **Step 3: Implement the store**

`frontend_app/src/stores/session.ts`:
```ts
import { create } from "zustand";
import type { ServerEvent } from "../lib/events";
import type { Message, MessageStatus } from "../lib/types";
import type { SocketStatus } from "../lib/ws";

export interface UiMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: MessageStatus | "pending";
  error?: string;
  errorCode?: string;
  clientId?: string;
}

export interface Notice {
  id: string;
  text: string;
}

export interface SessionData {
  conversationId: string | null;
  messages: UiMessage[];
  streamingId: string | null;
  notices: Notice[];
  socketStatus: SocketStatus;
}

let noticeSeq = 0;
const notice = (text: string): Notice => ({ id: `n${++noticeSeq}`, text });

export function toUiMessages(messages: Message[]): UiMessage[] {
  return messages
    .filter((m) => m.role !== "system")
    .map((m) => ({ id: m.id, role: m.role as "user" | "assistant", content: m.content, status: m.status }));
}

export function applyEvent(data: SessionData, ev: ServerEvent): SessionData {
  const has = (id: string) => data.messages.some((m) => m.id === id);
  switch (ev.type) {
    case "message_start": {
      if (ev.conversation_id !== data.conversationId) return data;
      const messages = data.messages.map((m) =>
        ev.client_id && m.clientId === ev.client_id ? { ...m, id: ev.user_message_id, status: "complete" as const } : m,
      );
      messages.push({ id: ev.message_id, role: "assistant", content: "", status: "streaming" });
      return { ...data, messages, streamingId: ev.message_id };
    }
    case "token":
      if (!has(ev.message_id)) return data;
      return {
        ...data,
        messages: data.messages.map((m) => (m.id === ev.message_id ? { ...m, content: m.content + ev.text } : m)),
      };
    case "message_end":
      if (!has(ev.message_id)) return data;
      return {
        ...data,
        streamingId: data.streamingId === ev.message_id ? null : data.streamingId,
        messages: data.messages.map((m) => (m.id === ev.message_id ? { ...m, status: ev.status } : m)),
      };
    case "error":
      if (ev.message_id && has(ev.message_id)) {
        return {
          ...data,
          messages: data.messages.map((m) =>
            m.id === ev.message_id ? { ...m, status: "error", error: ev.message, errorCode: ev.code } : m,
          ),
        };
      }
      return { ...data, notices: [...data.notices, notice(ev.message)] };
    case "provider_switched":
      return {
        ...data,
        notices: [...data.notices, notice(`${ev.from_provider} was unavailable, so ${ev.to_provider} answered instead.`)],
      };
    case "conversation_updated":
      return data;
  }
}

interface SessionState extends SessionData {
  setConversation(id: string | null, messages: UiMessage[]): void;
  addPendingUser(text: string, clientId: string): void;
  apply(ev: ServerEvent): void;
  setSocketStatus(status: SocketStatus): void;
  dismissNotice(id: string): void;
}

export const useSession = create<SessionState>()((set) => ({
  conversationId: null,
  messages: [],
  streamingId: null,
  notices: [],
  socketStatus: "connecting",
  setConversation: (conversationId, messages) =>
    set({
      conversationId,
      messages,
      streamingId: messages.find((m) => m.status === "streaming")?.id ?? null,
    }),
  addPendingUser: (text, clientId) =>
    set((s) => ({
      messages: [...s.messages, { id: `pending_${clientId}`, role: "user", content: text, status: "pending", clientId }],
    })),
  apply: (ev) => set((s) => applyEvent(s, ev)),
  setSocketStatus: (socketStatus) => set({ socketStatus }),
  dismissNotice: (id) => set((s) => ({ notices: s.notices.filter((n) => n.id !== id) })),
}));
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend_app && pnpm test src/stores`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add frontend_app/src/stores/session.ts frontend_app/src/stores/session.test.ts
git commit -m "feat(ui): add session store with pure websocket event reducer" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: UI primitives, appearance store, boot screen and app shell

**Files:**
- Create: `frontend_app/src/ui/cn.ts`, `IconButton.tsx`, `Button.tsx`, `Switch.tsx`, `Field.tsx`; `frontend_app/src/stores/ui.ts`; `frontend_app/src/features/shell/AppShell.tsx`, `Rail.tsx`, `Boot.tsx`, `ErrorBoundary.tsx`, `useSessionEvents.ts`
- Modify: `frontend_app/src/App.tsx` (full replacement), `frontend_app/src/main.tsx` (full replacement), `frontend_app/src/App.test.tsx` (full replacement)
- Test: `frontend_app/src/features/shell/Rail.test.tsx`

**Interfaces:**
- Consumes: `waitForBackend`, `pushStoredKeys`, `getSocket`, `useSession`
- Produces:
  - `useUi` (Zustand), with state `screen: "conversation" | "settings"`, `threadsOpen`, `theme: ThemePref` and `motion: MotionPref`, and actions `setScreen`, `toggleThreads`, `setThreadsOpen`, `setTheme` and `setMotion`
  - `applyStoredAppearance()`, `resolveTheme(p)` and `resolveMotion(p)`
  - `<AppShell/>` renders `ConversationView` / `SettingsView` (created in Tasks 16 and 17; until then AppShell imports the placeholders created in Step 3)
  - `cn(...classes)`

- [ ] **Step 1: Write the failing tests**

`frontend_app/src/features/shell/Rail.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Rail } from "./Rail";
import { useUi } from "../../stores/ui";

beforeEach(() => useUi.setState({ screen: "conversation", threadsOpen: false, theme: "light", motion: "system" }));

test("settings button switches screen and back", async () => {
  render(<Rail />);
  await userEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(useUi.getState().screen).toBe("settings");
  await userEvent.click(screen.getByRole("button", { name: "Aethel" }));
  expect(useUi.getState().screen).toBe("conversation");
});

test("threads button toggles the threads panel", async () => {
  render(<Rail />);
  await userEvent.click(screen.getByRole("button", { name: "Conversations" }));
  expect(useUi.getState().threadsOpen).toBe(true);
});

test("theme toggle flips data-theme on <html>", async () => {
  render(<Rail />);
  await userEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(useUi.getState().theme).toBe("dark");
});
```

`frontend_app/src/App.test.tsx` (replacement):
```tsx
import { render, screen } from "@testing-library/react";
import { App } from "./App";

vi.mock("./lib/api", async (orig) => ({
  ...(await orig<typeof import("./lib/api")>()),
  waitForBackend: () => new Promise(() => {}),
}));

test("shows the waking-up screen while the backend boots", () => {
  render(<App />);
  expect(screen.getByText("Aethel")).toBeInTheDocument();
  expect(screen.getByText(/waking up/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend_app && pnpm test`
Expected: FAIL (cannot resolve `./Rail`, `../../stores/ui`)

- [ ] **Step 3: Implement the primitives, the store and the shell**

`frontend_app/src/ui/cn.ts`:
```ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export const cn = (...classes: ClassValue[]) => twMerge(clsx(classes));
```

`frontend_app/src/ui/IconButton.tsx`:
```tsx
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cn } from "./cn";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  active?: boolean;
  children: ReactNode;
}

export const IconButton = forwardRef<HTMLButtonElement, Props>(function IconButton(
  { label, active, className, children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      aria-label={label}
      title={label}
      className={cn(
        "grid size-9 place-items-center rounded-[10px] border border-transparent text-muted transition-colors duration-[var(--dur-fast)]",
        "hover:border-hairline hover:text-ink focus-visible:outline-2 focus-visible:outline-accent",
        active && "border-hairline bg-paper text-ink",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
});
```

`frontend_app/src/ui/Button.tsx`:
```tsx
import type { ButtonHTMLAttributes } from "react";
import { cn } from "./cn";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "quiet" | "danger";
}

export function Button({ variant = "quiet", className, type = "button", ...rest }: Props) {
  return (
    <button
      type={type}
      className={cn(
        "inline-flex h-9 items-center gap-2 rounded-full px-4 text-[13px] font-medium transition-colors duration-[var(--dur-fast)] disabled:opacity-40",
        variant === "primary" && "bg-ink text-canvas hover:opacity-90",
        variant === "quiet" && "border border-hairline bg-paper text-ink hover:border-muted",
        variant === "danger" && "border border-hairline bg-paper text-accent hover:border-accent",
        className,
      )}
      {...rest}
    />
  );
}
```

`frontend_app/src/ui/Switch.tsx`:
```tsx
import * as RadixSwitch from "@radix-ui/react-switch";

interface Props {
  checked: boolean;
  onCheckedChange: (value: boolean) => void;
  label: string;
}

export function Switch({ checked, onCheckedChange, label }: Props) {
  return (
    <RadixSwitch.Root
      checked={checked}
      onCheckedChange={onCheckedChange}
      aria-label={label}
      className="relative h-6 w-11 shrink-0 rounded-full border border-hairline bg-well transition-colors data-[state=checked]:border-accent data-[state=checked]:bg-accent"
    >
      <RadixSwitch.Thumb className="block size-5 translate-x-0.5 rounded-full bg-paper shadow transition-transform duration-[var(--dur-base)] ease-[var(--ease-out)] data-[state=checked]:translate-x-[22px]" />
    </RadixSwitch.Root>
  );
}
```

`frontend_app/src/ui/Field.tsx`:
```tsx
import type { ReactNode } from "react";

interface Props {
  label: string;
  hint?: string;
  htmlFor?: string;
  children: ReactNode;
}

export function Field({ label, hint, htmlFor, children }: Props) {
  return (
    <div className="flex items-start justify-between gap-6 py-4">
      <div className="min-w-0">
        <label htmlFor={htmlFor} className="text-[13.5px] font-medium text-ink">
          {label}
        </label>
        {hint && <p className="mt-0.5 text-[12.5px] leading-5 text-muted">{hint}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

export const inputClass =
  "h-9 w-72 rounded-lg border border-hairline bg-paper px-3 text-[13.5px] text-ink placeholder:text-faint outline-none focus:border-muted";
```

`frontend_app/src/stores/ui.ts`:
```ts
import { create } from "zustand";

export type Screen = "conversation" | "settings";
export type ThemePref = "light" | "dark" | "system";
export type MotionPref = "system" | "full" | "reduced";

const THEME_KEY = "aethel_theme";
const MOTION_KEY = "aethel_motion";

function read<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = localStorage.getItem(key);
    return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable */
  }
}

export function resolveTheme(pref: ThemePref): "light" | "dark" {
  if (pref !== "system") return pref;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function resolveMotion(pref: MotionPref): "full" | "reduced" {
  if (pref !== "system") return pref;
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "reduced" : "full";
}

function applyAppearance(theme: ThemePref, motion: MotionPref) {
  const root = document.documentElement;
  root.dataset.theme = resolveTheme(theme);
  root.dataset.motion = resolveMotion(motion);
}

const storedTheme = () => read(THEME_KEY, ["light", "dark", "system"] as const, "system");
const storedMotion = () => read(MOTION_KEY, ["system", "full", "reduced"] as const, "system");

export function applyStoredAppearance() {
  applyAppearance(storedTheme(), storedMotion());
}

interface UiState {
  screen: Screen;
  threadsOpen: boolean;
  theme: ThemePref;
  motion: MotionPref;
  setScreen(screen: Screen): void;
  toggleThreads(): void;
  setThreadsOpen(open: boolean): void;
  setTheme(theme: ThemePref): void;
  setMotion(motion: MotionPref): void;
}

export const useUi = create<UiState>()((set, get) => ({
  screen: "conversation",
  threadsOpen: false,
  theme: storedTheme(),
  motion: storedMotion(),
  setScreen: (screen) => set({ screen, threadsOpen: false }),
  toggleThreads: () => set((s) => ({ threadsOpen: !s.threadsOpen })),
  setThreadsOpen: (threadsOpen) => set({ threadsOpen }),
  setTheme: (theme) => {
    write(THEME_KEY, theme);
    applyAppearance(theme, get().motion);
    set({ theme });
  },
  setMotion: (motion) => {
    write(MOTION_KEY, motion);
    applyAppearance(get().theme, motion);
    set({ motion });
  },
}));

/** Re-apply when the OS theme / motion preference changes and we follow it. */
export function watchSystemAppearance(): () => void {
  const queries = ["(prefers-color-scheme: dark)", "(prefers-reduced-motion: reduce)"].map((q) => window.matchMedia(q));
  const sync = () => applyAppearance(useUi.getState().theme, useUi.getState().motion);
  queries.forEach((q) => q.addEventListener("change", sync));
  return () => queries.forEach((q) => q.removeEventListener("change", sync));
}
```

`frontend_app/src/features/shell/Rail.tsx`:
```tsx
import { MessagesSquare, Moon, PenLine, Settings2, Sun } from "lucide-react";
import { IconButton } from "../../ui/IconButton";
import { resolveTheme, useUi } from "../../stores/ui";
import { useSession } from "../../stores/session";
import { cn } from "../../ui/cn";

export function Rail() {
  const { screen, threadsOpen, theme, setScreen, toggleThreads, setTheme } = useUi();
  const dark = resolveTheme(theme) === "dark";

  return (
    <nav className="relative z-20 flex h-full w-16 flex-col items-center gap-3 border-r border-hairline bg-canvas py-5">
      <button
        type="button"
        aria-label="Aethel"
        title="Aethel"
        onClick={() => setScreen("conversation")}
        className={cn(
          "grid size-9 place-items-center rounded-full bg-paper font-display text-[19px] leading-none text-ink transition-shadow duration-[var(--dur-base)]",
          screen === "conversation"
            ? "shadow-[0_0_0_2px_var(--canvas),0_0_0_3.5px_var(--accent)]"
            : "shadow-[0_0_0_1px_var(--hairline)]",
        )}
      >
        Æ
      </button>
      <div className="flex-1" />
      <IconButton label="New conversation" onClick={() => { useSession.getState().setConversation(null, []); setScreen("conversation"); }}>
        <PenLine size={17} strokeWidth={1.5} />
      </IconButton>
      <IconButton label="Conversations" active={threadsOpen} onClick={toggleThreads}>
        <MessagesSquare size={17} strokeWidth={1.5} />
      </IconButton>
      <IconButton label={dark ? "Switch to light theme" : "Switch to dark theme"} onClick={() => setTheme(dark ? "light" : "dark")}>
        {dark ? <Sun size={17} strokeWidth={1.5} /> : <Moon size={17} strokeWidth={1.5} />}
      </IconButton>
      <IconButton label="Settings" active={screen === "settings"} onClick={() => setScreen("settings")}>
        <Settings2 size={17} strokeWidth={1.5} />
      </IconButton>
    </nav>
  );
}
```

`frontend_app/src/features/shell/useSessionEvents.ts`:
```ts
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getSocket } from "../../lib/session";
import { useSession } from "../../stores/session";

/** Pipes websocket events into the session store; refreshes the thread list. */
export function useSessionEvents() {
  const qc = useQueryClient();
  useEffect(() => {
    const socket = getSocket();
    const offEvents = socket.subscribe((ev) => {
      useSession.getState().apply(ev);
      if (ev.type === "conversation_updated" || ev.type === "message_end") {
        void qc.invalidateQueries({ queryKey: ["conversations"] });
      }
    });
    const offStatus = socket.onStatus((s) => useSession.getState().setSocketStatus(s));
    return () => {
      offEvents();
      offStatus();
    };
  }, [qc]);
}
```

`frontend_app/src/features/shell/Boot.tsx`:
```tsx
import { useEffect, useState, type ReactNode } from "react";
import { waitForBackend } from "../../lib/api";
import { pushStoredKeys } from "../../lib/keys";

type State = { phase: "waiting" } | { phase: "ready" } | { phase: "failed"; message: string };

export function Boot({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ phase: "waiting" });

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        await waitForBackend();
        await pushStoredKeys();
        if (alive) setState({ phase: "ready" });
      } catch (err) {
        if (alive) setState({ phase: "failed", message: err instanceof Error ? err.message : String(err) });
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (state.phase === "ready") return <>{children}</>;
  return (
    <div className="grain flex h-full flex-col items-center justify-center gap-4 bg-canvas text-ink">
      <h1 className="font-display text-6xl leading-none">Aethel</h1>
      {state.phase === "waiting" ? (
        <p className="flex items-center gap-2 text-[13px] text-muted">
          <span className="breathe inline-block size-1.5 rounded-full bg-accent" />
          waking up…
        </p>
      ) : (
        <p role="alert" className="max-w-md text-center font-voice text-[16px] text-muted">
          {state.message}
        </p>
      )}
    </div>
  );
}
```

`frontend_app/src/features/shell/ErrorBoundary.tsx`:
```tsx
import { Component, type ErrorInfo, type ReactNode } from "react";

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Aethel UI error", error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="grain flex h-full flex-col items-center justify-center gap-4 bg-canvas p-8 text-ink">
        <h1 className="font-display text-4xl">Something tore.</h1>
        <pre className="max-w-xl overflow-auto rounded-lg border border-hairline bg-paper p-4 text-[12px] text-muted">
          {String(this.state.error)}
        </pre>
        <button className="rounded-full bg-ink px-4 py-2 text-[13px] text-canvas" onClick={() => window.location.reload()}>
          Reload
        </button>
      </div>
    );
  }
}
```

`frontend_app/src/features/shell/AppShell.tsx`:
```tsx
import { useEffect } from "react";
import { MotionConfig } from "motion/react";
import { Toaster } from "sonner";
import { Rail } from "./Rail";
import { useSessionEvents } from "./useSessionEvents";
import { resolveMotion, useUi, watchSystemAppearance } from "../../stores/ui";
import { ConversationView } from "../conversation/ConversationView";
import { SettingsView } from "../settings/SettingsView";

export function AppShell() {
  const { screen, motion } = useUi();
  useSessionEvents();
  useEffect(() => watchSystemAppearance(), []);

  return (
    <MotionConfig reducedMotion={resolveMotion(motion) === "reduced" ? "always" : "never"}>
      <div className="grain flex h-full bg-canvas text-ink">
        <Rail />
        <main className="relative min-w-0 flex-1">
          {screen === "conversation" ? <ConversationView /> : <SettingsView />}
        </main>
        <Toaster
          position="bottom-right"
          toastOptions={{
            className: "!bg-paper !text-ink !border-hairline !font-sans !text-[13px]",
          }}
        />
      </div>
    </MotionConfig>
  );
}
```

Temporary placeholders, so the shell compiles before Tasks 16 and 17 replace them:

`frontend_app/src/features/conversation/ConversationView.tsx`:
```tsx
export function ConversationView() {
  return <div className="p-10 font-display text-4xl">Conversation</div>;
}
```

`frontend_app/src/features/settings/SettingsView.tsx`:
```tsx
export function SettingsView() {
  return <div className="p-10 font-display text-4xl">Settings</div>;
}
```

`frontend_app/src/App.tsx` (replacement):
```tsx
import { Boot } from "./features/shell/Boot";
import { AppShell } from "./features/shell/AppShell";

export function App() {
  return (
    <Boot>
      <AppShell />
    </Boot>
  );
}
```

`frontend_app/src/main.tsx` (replacement):
```tsx
import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./fonts";
import "./styles/index.css";
import { App } from "./App";
import { ErrorBoundary } from "./features/shell/ErrorBoundary";
import { applyStoredAppearance } from "./stores/ui";

applyStoredAppearance();
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
```

- [ ] **Step 4: Run the tests and the build**

Run: `cd frontend_app && pnpm test && pnpm build`
Expected: all pass; build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend_app/src
git commit -m "feat(ui): add Paper & Ink app shell, rail, boot screen and appearance store" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Conversation view: threads, messages with ink settling, prompt box

**Files:**
- Create: `frontend_app/src/features/conversation/useConversations.ts`, `InkText.tsx`, `PersonaMessage.tsx`, `UserMessage.tsx`, `MessageList.tsx`, `PromptBox.tsx`, `ThreadsPanel.tsx`, `Notices.tsx`, `greeting.ts`
- Modify: `frontend_app/src/features/conversation/ConversationView.tsx` (full replacement of the placeholder), `frontend_app/src/features/shell/AppShell.tsx` (render `<ThreadsPanel/>` inside `<main>`)
- Test: `frontend_app/src/features/conversation/conversation.test.tsx`

**Interfaces:**
- Consumes: `api`, `getSocket`, `useSession`, `toUiMessages`, `newClientId`, `useUi`, and the types
- Produces:
  - `useConversations()` and `useSendMessage(): (text: string) => Promise<void>`
  - `openConversation(id): Promise<void>`
  - `useDeleteConversation()`
  - `greeting(date?: Date): string`
  - `formatRelative(iso: string, now?: Date): string`

- [ ] **Step 1: Write the failing tests**

`frontend_app/src/features/conversation/conversation.test.tsx`:
```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// ESM exports can't be spied on reliably; mock the module instead.
const apiMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock }));
import { InkText } from "./InkText";
import { PersonaMessage } from "./PersonaMessage";
import { PromptBox } from "./PromptBox";
import { ConversationView } from "./ConversationView";
import { greeting, formatRelative } from "./greeting";
import { setSocketForTests } from "../../lib/session";
import { useSession } from "../../stores/session";

const wrap = (ui: ReactNode) =>
  render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);

test("InkText wraps each word in an ink-word span and keeps whitespace", () => {
  const { container } = render(<InkText text={"Hello  wide\nworld"} />);
  const words = container.querySelectorAll(".ink-word");
  expect([...words].map((w) => w.textContent)).toEqual(["Hello", "wide", "world"]);
  expect(container.textContent).toBe("Hello  wide\nworld");
});

test("PersonaMessage shows markdown when complete and an alert on error", () => {
  const { rerender } = render(
    <PersonaMessage message={{ id: "a", role: "assistant", content: "**bold** text", status: "complete" }} />,
  );
  expect(screen.getByText("bold").tagName).toBe("STRONG");
  rerender(
    <PersonaMessage message={{ id: "a", role: "assistant", content: "", status: "error", error: "groq:x: no API key", errorCode: "no_provider" }} />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("no API key");
  expect(screen.getByRole("button", { name: /open settings/i })).toBeInTheDocument();
});

test("PromptBox sends on Enter, not on Shift+Enter, and shows Stop while streaming", async () => {
  const onSend = vi.fn();
  const onStop = vi.fn();
  const { rerender } = render(<PromptBox onSend={onSend} onStop={onStop} streaming={false} />);
  const box = screen.getByRole("textbox", { name: "Message" });
  await userEvent.type(box, "line one{Shift>}{Enter}{/Shift}line two");
  expect(onSend).not.toHaveBeenCalled();
  await userEvent.type(box, "{Enter}");
  expect(onSend).toHaveBeenCalledWith("line one\nline two");
  expect(box).toHaveValue("");
  rerender(<PromptBox onSend={onSend} onStop={onStop} streaming />);
  await userEvent.click(screen.getByRole("button", { name: "Stop" }));
  expect(onStop).toHaveBeenCalled();
});

test("sending from the empty state creates a conversation then sends user_message", async () => {
  const sent: unknown[] = [];
  setSocketForTests({ send: (e) => sent.push(e), subscribe: () => () => {}, onStatus: () => () => {} });
  useSession.setState({ conversationId: null, messages: [], streamingId: null, notices: [], socketStatus: "open" });
  apiMock.mockImplementation(async (path: string) => {
    if (path === "/api/conversations") return { id: "c9", title: "", persona_id: "aethel", created_at: "", updated_at: "" };
    return [];
  });
  wrap(<ConversationView />);
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/good (morning|afternoon|evening)|hello/i);
  await userEvent.type(screen.getByRole("textbox", { name: "Message" }), "hi Aethel{Enter}");
  await waitFor(() =>
    expect(sent).toEqual([{ type: "user_message", conversation_id: "c9", text: "hi Aethel", client_id: expect.any(String) }]),
  );
  const list = screen.getByRole("log");
  expect(within(list).getByText("hi Aethel")).toBeInTheDocument();
  apiMock.mockReset();
});

test("greeting and relative time helpers", () => {
  expect(greeting(new Date(2026, 0, 1, 8))).toBe("Good morning.");
  expect(greeting(new Date(2026, 0, 1, 14))).toBe("Good afternoon.");
  expect(greeting(new Date(2026, 0, 1, 21))).toBe("Good evening.");
  const now = new Date("2026-09-23T12:00:00Z");
  expect(formatRelative("2026-09-23T11:59:30Z", now)).toBe("just now");
  expect(formatRelative("2026-09-23T09:00:00Z", now)).toBe("3 hours ago");
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend_app && pnpm test src/features/conversation`
Expected: FAIL (cannot resolve `./InkText` etc.)

- [ ] **Step 3: Implement the conversation feature**

`frontend_app/src/features/conversation/greeting.ts`:
```ts
export function greeting(date = new Date()): string {
  const h = date.getHours();
  if (h < 5) return "Hello, night owl.";
  if (h < 12) return "Good morning.";
  if (h < 18) return "Good afternoon.";
  return "Good evening.";
}

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

export function formatRelative(iso: string, now = new Date()): string {
  const seconds = Math.round((new Date(iso).getTime() - now.getTime()) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 60) return "just now";
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
  return rtf.format(Math.round(seconds / 86400), "day");
}
```

`frontend_app/src/features/conversation/useConversations.ts`:
```ts
import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { newClientId } from "../../lib/ids";
import { getSocket } from "../../lib/session";
import type { Conversation, Message } from "../../lib/types";
import { toUiMessages, useSession } from "../../stores/session";

export function useConversations() {
  return useQuery({ queryKey: ["conversations"], queryFn: () => api<Conversation[]>("/api/conversations") });
}

export async function openConversation(id: string): Promise<void> {
  const messages = await api<Message[]>(`/api/conversations/${id}/messages`);
  useSession.getState().setConversation(id, toUiMessages(messages));
}

export function useDeleteConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/api/conversations/${id}`, { method: "DELETE" }),
    onSuccess: (_d, id) => {
      if (useSession.getState().conversationId === id) useSession.getState().setConversation(null, []);
      void qc.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}

export function useSendMessage() {
  const qc = useQueryClient();
  return useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      let conversationId = useSession.getState().conversationId;
      if (!conversationId) {
        const conv = await api<Conversation>("/api/conversations", { method: "POST", body: JSON.stringify({}) });
        useSession.getState().setConversation(conv.id, []);
        conversationId = conv.id;
        void qc.invalidateQueries({ queryKey: ["conversations"] });
      }
      const clientId = newClientId();
      useSession.getState().addPendingUser(trimmed, clientId);
      getSocket().send({ type: "user_message", conversation_id: conversationId, text: trimmed, client_id: clientId });
    },
    [qc],
  );
}
```

`frontend_app/src/features/conversation/InkText.tsx`:
```tsx
/** Streaming text: every word is its own span so new words "settle" in. Keys
 * are positional, so a word that grows token by token keeps its span. */
export function InkText({ text }: { text: string }) {
  const parts = text.split(/(\s+)/);
  return (
    <p className="whitespace-pre-wrap">
      {parts.map((part, i) =>
        part === "" || /^\s+$/.test(part) ? part : (
          <span key={i} className="ink-word">
            {part}
          </span>
        ),
      )}
    </p>
  );
}
```

`frontend_app/src/features/conversation/PersonaMessage.tsx`:
```tsx
import Markdown, { type Components } from "react-markdown";
import type { UiMessage } from "../../stores/session";
import { useUi } from "../../stores/ui";
import { InkText } from "./InkText";

// Each renderer drops react-markdown's `node` prop so it never reaches the DOM.
const components: Components = {
  p: ({ node: _n, ...props }) => <p className="mb-3 last:mb-0" {...props} />,
  a: ({ node: _n, ...props }) => <a className="text-accent underline decoration-accent/40 underline-offset-2" target="_blank" rel="noreferrer" {...props} />,
  ul: ({ node: _n, ...props }) => <ul className="mb-3 list-disc pl-5 last:mb-0" {...props} />,
  ol: ({ node: _n, ...props }) => <ol className="mb-3 list-decimal pl-5 last:mb-0" {...props} />,
  code: ({ node: _n, ...props }) => <code className="rounded bg-well px-1 py-0.5 font-mono text-[0.85em]" {...props} />,
  pre: ({ node: _n, ...props }) => <pre className="mb-3 overflow-x-auto rounded-lg border border-hairline bg-paper p-3 font-mono text-[13px] leading-5" {...props} />,
};

export function PersonaMessage({ message }: { message: UiMessage }) {
  const setScreen = useUi((s) => s.setScreen);
  return (
    <div className="max-w-[78%] font-voice text-[17px] leading-[1.55] text-ink-2">
      {message.status === "streaming" ? (
        message.content ? (
          <InkText text={message.content} />
        ) : (
          <span aria-label="Aethel is writing" className="inline-flex gap-1 pt-2">
            <span className="breathe size-1.5 rounded-full bg-faint" />
            <span className="breathe size-1.5 rounded-full bg-faint [animation-delay:200ms]" />
            <span className="breathe size-1.5 rounded-full bg-faint [animation-delay:400ms]" />
          </span>
        )
      ) : (
        message.content && <Markdown components={components}>{message.content}</Markdown>
      )}
      {message.status === "stopped" && <p className="mt-1 font-sans text-[12px] text-faint">stopped</p>}
      {message.status === "error" && (
        <div className="mt-1 flex flex-wrap items-center gap-3 font-sans">
          <p role="alert" className="text-[13px] text-accent">
            {message.error ?? "Something went wrong."}
          </p>
          {message.errorCode === "no_provider" && (
            <button type="button" className="text-[12.5px] text-muted underline underline-offset-2 hover:text-ink" onClick={() => setScreen("settings")}>
              Open settings
            </button>
          )}
        </div>
      )}
    </div>
  );
}
```

`frontend_app/src/features/conversation/UserMessage.tsx`:
```tsx
import type { UiMessage } from "../../stores/session";
import { cn } from "../../ui/cn";

export function UserMessage({ message }: { message: UiMessage }) {
  return (
    <div
      className={cn(
        "max-w-[70%] self-end whitespace-pre-wrap rounded-[16px_16px_4px_16px] bg-well px-3.5 py-2 text-[14px] leading-6 text-ink",
        message.status === "pending" && "opacity-70",
      )}
    >
      {message.content}
    </div>
  );
}
```

`frontend_app/src/features/conversation/MessageList.tsx`:
```tsx
import { useLayoutEffect, useRef } from "react";
import { motion } from "motion/react";
import { useSession } from "../../stores/session";
import { PersonaMessage } from "./PersonaMessage";
import { UserMessage } from "./UserMessage";

export function MessageList() {
  const messages = useSession((s) => s.messages);
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTo({ top: el.scrollHeight });
  }, [messages]);

  return (
    <div
      ref={ref}
      role="log"
      aria-live="polite"
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
      }}
      className="flex-1 overflow-y-auto"
    >
      <div className="mx-auto flex max-w-3xl flex-col gap-5 px-10 py-8">
        {messages.map((m) => (
          <motion.div
            key={m.id}
            layout="position"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ type: "spring", stiffness: 260, damping: 30 }}
            className="flex flex-col"
          >
            {m.role === "user" ? <UserMessage message={m} /> : <PersonaMessage message={m} />}
          </motion.div>
        ))}
      </div>
    </div>
  );
}
```

`frontend_app/src/features/conversation/PromptBox.tsx`:
```tsx
import { useLayoutEffect, useRef, useState } from "react";
import { ArrowUp, Square } from "lucide-react";

interface Props {
  onSend: (text: string) => void;
  onStop: () => void;
  streaming: boolean;
  placeholder?: string;
}

export function PromptBox({ onSend, onStop, streaming, placeholder = "Say something to Aethel…" }: Props) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [text]);

  const submit = () => {
    if (!text.trim() || streaming) return;
    onSend(text);
    setText("");
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="mx-auto mb-6 flex w-full max-w-3xl items-end gap-3 rounded-[14px] border border-hairline bg-paper px-4 py-3 transition-colors duration-[var(--dur-fast)] focus-within:border-muted"
    >
      <textarea
        ref={ref}
        aria-label="Message"
        rows={1}
        value={text}
        placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
        className="max-h-[220px] flex-1 resize-none bg-transparent py-1 text-[14.5px] leading-6 text-ink outline-none placeholder:text-faint"
      />
      {streaming ? (
        <button type="button" aria-label="Stop" onClick={onStop} className="grid size-8 place-items-center rounded-full border border-hairline text-ink hover:border-muted">
          <Square size={12} fill="currentColor" />
        </button>
      ) : (
        <button type="submit" aria-label="Send" disabled={!text.trim()} className="grid size-8 place-items-center rounded-full bg-ink text-canvas transition-opacity disabled:opacity-25">
          <ArrowUp size={16} strokeWidth={2} />
        </button>
      )}
    </form>
  );
}
```

`frontend_app/src/features/conversation/Notices.tsx`:
```tsx
import { useEffect } from "react";
import { toast } from "sonner";
import { useSession } from "../../stores/session";

/** Turns session notices (provider switches, protocol errors) into quiet toasts. */
export function Notices() {
  const notices = useSession((s) => s.notices);
  useEffect(() => {
    notices.forEach((n) => {
      toast(n.text);
      useSession.getState().dismissNotice(n.id);
    });
  }, [notices]);
  return null;
}
```

`frontend_app/src/features/conversation/ThreadsPanel.tsx`:
```tsx
import { AnimatePresence, motion } from "motion/react";
import { Plus, Trash2 } from "lucide-react";
import { useUi } from "../../stores/ui";
import { useSession } from "../../stores/session";
import { cn } from "../../ui/cn";
import { formatRelative } from "./greeting";
import { openConversation, useConversations, useDeleteConversation } from "./useConversations";

export function ThreadsPanel() {
  const { threadsOpen, setThreadsOpen } = useUi();
  const activeId = useSession((s) => s.conversationId);
  const { data: conversations = [] } = useConversations();
  const remove = useDeleteConversation();

  return (
    <AnimatePresence>
      {threadsOpen && (
        <motion.aside
          aria-label="Conversations"
          initial={{ opacity: 0, x: -16 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: -16 }}
          transition={{ type: "spring", stiffness: 260, damping: 30 }}
          className="absolute inset-y-0 left-0 z-10 flex w-[300px] flex-col border-r border-hairline bg-canvas shadow-lift"
        >
          <div className="flex items-center justify-between px-5 pt-6 pb-3">
            <h2 className="font-display text-[24px] leading-none">Conversations</h2>
            <button
              type="button"
              aria-label="Start a new conversation"
              onClick={() => {
                useSession.getState().setConversation(null, []);
                setThreadsOpen(false);
              }}
              className="grid size-8 place-items-center rounded-full border border-hairline text-muted hover:text-ink"
            >
              <Plus size={15} />
            </button>
          </div>
          <ul className="flex-1 overflow-y-auto px-2 pb-4">
            {conversations.length === 0 && <li className="px-3 py-6 font-voice text-[15px] italic text-muted">Nothing here yet.</li>}
            {conversations.map((c) => (
              <li key={c.id} className="group relative">
                <button
                  type="button"
                  onClick={async () => {
                    await openConversation(c.id);
                    setThreadsOpen(false);
                  }}
                  className={cn(
                    "w-full rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-paper",
                    c.id === activeId && "bg-well",
                  )}
                >
                  <span className="block truncate pr-7 text-[13.5px] text-ink">{c.title || "Untitled"}</span>
                  <span className="text-[11.5px] text-faint">{formatRelative(c.updated_at)}</span>
                </button>
                <button
                  type="button"
                  aria-label={`Delete ${c.title || "conversation"}`}
                  onClick={() => remove.mutate(c.id)}
                  className="absolute top-3 right-2 hidden text-faint hover:text-accent group-hover:block"
                >
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
```

`frontend_app/src/features/conversation/ConversationView.tsx` (replacement):
```tsx
import { getSocket } from "../../lib/session";
import { useSession } from "../../stores/session";
import { greeting } from "./greeting";
import { MessageList } from "./MessageList";
import { Notices } from "./Notices";
import { PromptBox } from "./PromptBox";
import { useSendMessage } from "./useConversations";

export function ConversationView() {
  const { conversationId, messages, streamingId, socketStatus } = useSession();
  const send = useSendMessage();
  const stop = () => streamingId && getSocket().send({ type: "stop_generation", message_id: streamingId });
  const status =
    socketStatus !== "open" ? "reconnecting…" : streamingId ? "writing…" : "here with you";

  const prompt = <PromptBox onSend={(t) => void send(t)} onStop={() => void stop()} streaming={!!streamingId} />;

  return (
    <div className="flex h-full flex-col bg-paper">
      <Notices />
      <header className="flex items-baseline gap-3 border-b border-hairline px-10 pt-6 pb-4">
        <span className="font-display text-[34px] leading-none">Aethel</span>
        <span className="flex items-center gap-1.5 text-[12px] text-muted">
          <span className={socketStatus === "open" ? "size-1.5 rounded-full bg-accent" : "breathe size-1.5 rounded-full bg-faint"} />
          {status}
        </span>
      </header>
      {conversationId === null && messages.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-10">
          <h1 className="font-display text-[48px] leading-none">{greeting()}</h1>
          <p className="mb-8 font-voice text-[18px] italic text-muted">What's on your mind?</p>
          {prompt}
        </div>
      ) : (
        <>
          <MessageList />
          <div className="px-10">{prompt}</div>
        </>
      )}
    </div>
  );
}
```

In the empty state, the first send creates the conversation, which switches the view to `MessageList` (the `role="log"` element the test reads).

`frontend_app/src/features/shell/AppShell.tsx`: import `ThreadsPanel` from `../conversation/ThreadsPanel` and render it as the first child of `<main>`:
```tsx
        <main className="relative min-w-0 flex-1">
          <ThreadsPanel />
          {screen === "conversation" ? <ConversationView /> : <SettingsView />}
        </main>
```

- [ ] **Step 4: Run the tests and the build**

Run: `cd frontend_app && pnpm test && pnpm build`
Expected: all pass; build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend_app/src
git commit -m "feat(ui): add conversation view with ink-settling replies, threads panel and prompt box" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: Settings view: mode, providers and keys, models per role, local model, appearance

**Files:**
- Create: `frontend_app/src/features/settings/useSettings.ts`, `ModeSection.tsx`, `ProvidersSection.tsx`, `RolesSection.tsx`, `LocalModelSection.tsx`, `AppearanceSection.tsx`, `Section.tsx`
- Modify: `frontend_app/src/features/settings/SettingsView.tsx` (full replacement of the placeholder)
- Test: `frontend_app/src/features/settings/settings.test.tsx`

**Interfaces:**
- Consumes: `api`, `saveKey`, the types, `useUi`, and the UI primitives
- Produces:
  - `useSettings()`, a query on `["settings"]`
  - `useUpdateSettings()`, a mutation that PATCHes `/api/settings` and writes the result to the cache
  - `useProviders()`, a query on `["providers"]`
  - `useModels(provider, enabled)`, a query on `["models", provider]`

- [ ] **Step 1: Write the failing tests**

`frontend_app/src/features/settings/settings.test.tsx`:
```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SettingsView } from "./SettingsView";
import type { AppSettings, ProviderInfo } from "../../lib/types";

const apiMock = vi.hoisted(() => vi.fn());
const saveKeyMock = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), api: apiMock }));
vi.mock("../../lib/keys", () => ({ saveKey: saveKeyMock, pushStoredKeys: vi.fn() }));

const settings: AppSettings = {
  roles: {
    chat: [{ provider: "groq", model: "llama-3.3-70b-versatile" }],
    agent: [{ provider: "gemini", model: "gemini-2.5-flash" }],
    vision: [{ provider: "gemini", model: "gemini-2.5-flash" }],
  },
  custom_base_url: "",
  private_mode: false,
  internet: false,
  local_llm: { model_path: "", context_size: 0, threads: 4, gpu_layers: 99 },
  temperature: 0.8,
  max_tokens: 1024,
  history_window: 24,
};
const providers: ProviderInfo[] = [
  { id: "groq", label: "Groq", needs_key: true, has_key: true, base_url: "" },
  { id: "gemini", label: "Google Gemini", needs_key: true, has_key: false, base_url: "" },
  { id: "openrouter", label: "OpenRouter", needs_key: true, has_key: false, base_url: "" },
  { id: "custom", label: "Custom (OpenAI-compatible)", needs_key: true, has_key: false, base_url: "" },
  { id: "local", label: "Local (llama.cpp)", needs_key: false, has_key: true, base_url: "" },
];

let patches: unknown[];
beforeEach(() => {
  patches = [];
  apiMock.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/api/settings" && init?.method === "PATCH") {
      const patch = JSON.parse(String(init.body));
      patches.push(patch);
      return { ...settings, ...patch };
    }
    if (path === "/api/settings") return settings;
    if (path === "/api/providers") return providers;
    if (path.endsWith("/models")) return { models: ["m1", "m2"] };
    return {};
  });
  saveKeyMock.mockResolvedValue(undefined);
});
afterEach(() => {
  apiMock.mockReset();
  saveKeyMock.mockReset();
});

const renderSettings = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SettingsView />
    </QueryClientProvider>,
  );

test("private mode switch patches settings", async () => {
  renderSettings();
  await userEvent.click(await screen.findByRole("switch", { name: "Private mode" }));
  await waitFor(() => expect(patches).toContainEqual({ private_mode: true }));
});

test("saving a provider key goes through saveKey", async () => {
  renderSettings();
  const row = await screen.findByTestId("key-row-gemini");
  await userEvent.type(within(row).getByLabelText("Google Gemini API key"), "AIza-test");
  await userEvent.click(within(row).getByRole("button", { name: "Save" }));
  expect(saveKeyMock).toHaveBeenCalledWith("gemini", "AIza-test");
});

test("adding a fallback to the chat role and saving sends the whole chain", async () => {
  renderSettings();
  const chat = await screen.findByTestId("role-chat");
  await userEvent.click(within(chat).getByRole("button", { name: "Add fallback" }));
  const models = within(chat).getAllByLabelText("Model");
  await userEvent.clear(models[1]);
  await userEvent.type(models[1], "local");
  await userEvent.selectOptions(within(chat).getAllByLabelText("Provider")[1], "local");
  await userEvent.click(within(chat).getByRole("button", { name: "Save chat models" }));
  await waitFor(() =>
    expect(patches).toContainEqual({
      roles: { chat: [{ provider: "groq", model: "llama-3.3-70b-versatile" }, { provider: "local", model: "local" }] },
    }),
  );
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend_app && pnpm test src/features/settings`
Expected: FAIL (the placeholder has no switch)

- [ ] **Step 3: Implement the settings feature**

`frontend_app/src/features/settings/useSettings.ts`:
```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { AppSettings, ProviderInfo } from "../../lib/types";

export function useSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: () => api<AppSettings>("/api/settings") });
}

export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<AppSettings> | Record<string, unknown>) =>
      api<AppSettings>("/api/settings", { method: "PATCH", body: JSON.stringify(patch) }),
    onSuccess: (next) => qc.setQueryData(["settings"], next),
  });
}

export function useProviders() {
  return useQuery({ queryKey: ["providers"], queryFn: () => api<ProviderInfo[]>("/api/providers") });
}

export function useModels(provider: string, enabled: boolean) {
  return useQuery({
    queryKey: ["models", provider],
    queryFn: () => api<{ models: string[] }>(`/api/providers/${provider}/models`).then((r) => r.models),
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  });
}
```

`frontend_app/src/features/settings/Section.tsx`:
```tsx
import type { ReactNode } from "react";

export function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <section className="border-t border-hairline py-8 first:border-t-0">
      <h2 className="font-display text-[26px] leading-none text-ink">{title}</h2>
      {description && <p className="mt-2 max-w-xl font-voice text-[15px] text-muted">{description}</p>}
      <div className="mt-4 divide-y divide-hairline">{children}</div>
    </section>
  );
}
```

`frontend_app/src/features/settings/ModeSection.tsx`:
```tsx
import { Field } from "../../ui/Field";
import { Switch } from "../../ui/Switch";
import type { AppSettings } from "../../lib/types";
import { Section } from "./Section";
import { useUpdateSettings } from "./useSettings";

export function ModeSection({ settings }: { settings: AppSettings }) {
  const update = useUpdateSettings();
  return (
    <Section title="Mode" description="Hybrid uses your cloud keys for thinking; voice, memory and skills always stay on this computer.">
      <Field label="Private mode" hint="Everything runs on local models. Nothing leaves this computer.">
        <Switch label="Private mode" checked={settings.private_mode} onCheckedChange={(v) => update.mutate({ private_mode: v })} />
      </Field>
    </Section>
  );
}
```

`frontend_app/src/features/settings/ProvidersSection.tsx`:
```tsx
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "../../ui/Button";
import { Field, inputClass } from "../../ui/Field";
import { saveKey, type KeyProvider } from "../../lib/keys";
import type { AppSettings, ProviderInfo } from "../../lib/types";
import { Section } from "./Section";
import { useProviders, useUpdateSettings } from "./useSettings";

const HINTS: Record<KeyProvider, string> = {
  groq: "Very fast chat models. console.groq.com/keys",
  gemini: "Planning and vision. aistudio.google.com/apikey",
  openrouter: "One key, many models. openrouter.ai/keys",
  custom: "Any OpenAI-compatible server.",
};

function KeyRow({ provider }: { provider: ProviderInfo }) {
  const [value, setValue] = useState("");
  const qc = useQueryClient();
  const id = provider.id as KeyProvider;
  const done = (msg: string) => {
    setValue("");
    toast(msg);
    void qc.invalidateQueries({ queryKey: ["providers"] });
    void qc.invalidateQueries({ queryKey: ["models", id] });
  };
  return (
    <div data-testid={`key-row-${id}`}>
      <Field label={provider.label} hint={HINTS[id]} htmlFor={`key-${id}`}>
        <div className="flex items-center gap-2">
          <span className={provider.has_key ? "size-1.5 rounded-full bg-accent" : "size-1.5 rounded-full bg-hairline"} title={provider.has_key ? "Key saved" : "No key"} />
          <input
            id={`key-${id}`}
            aria-label={`${provider.label} API key`}
            type="password"
            autoComplete="off"
            placeholder={provider.has_key ? "•••••••• saved" : "Paste API key"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className={`${inputClass} w-56`}
          />
          <Button onClick={async () => { await saveKey(id, value.trim()); done(`${provider.label} key saved`); }} disabled={!value.trim()}>
            Save
          </Button>
          {provider.has_key && (
            <Button variant="danger" onClick={async () => { await saveKey(id, null); done(`${provider.label} key removed`); }}>
              Remove
            </Button>
          )}
        </div>
      </Field>
    </div>
  );
}

export function ProvidersSection({ settings }: { settings: AppSettings }) {
  const { data: providers = [] } = useProviders();
  const update = useUpdateSettings();
  const [baseUrl, setBaseUrl] = useState(settings.custom_base_url);
  return (
    <Section title="Providers" description="Keys are kept in Windows Credential Manager and only ever held in memory by Aethel.">
      {providers.filter((p) => p.needs_key).map((p) => <KeyRow key={p.id} provider={p} />)}
      <Field label="Custom endpoint URL" hint="For the Custom provider, e.g. http://192.168.1.20:8080/v1" htmlFor="custom-url">
        <div className="flex gap-2">
          <input id="custom-url" className={inputClass} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://…/v1" />
          <Button onClick={() => update.mutate({ custom_base_url: baseUrl.trim() })}>Save</Button>
        </div>
      </Field>
    </Section>
  );
}
```

`frontend_app/src/features/settings/RolesSection.tsx`:
```tsx
import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { api } from "../../lib/api";
import type { AppSettings, ProviderId, ProviderInfo, ProviderTestResult, RouteEntry } from "../../lib/types";
import { Button } from "../../ui/Button";
import { IconButton } from "../../ui/IconButton";
import { inputClass } from "../../ui/Field";
import { Section } from "./Section";
import { useModels, useProviders, useUpdateSettings } from "./useSettings";

// Stable identity: a fresh [] each render would re-trigger RoleEditor's reset effect forever.
const NO_ENTRIES: RouteEntry[] = [];

const ROLES: { id: string; label: string; hint: string }[] = [
  { id: "chat", label: "Conversation", hint: "Everyday talking. Fast models feel best here." },
  { id: "agent", label: "Agent", hint: "Plans and carries out tasks (Phase 1)." },
  { id: "vision", label: "Vision", hint: "Reads screenshots when an app can't be read directly (Phase 1)." },
];

function ModelOptions({ provider }: { provider: ProviderInfo }) {
  const { data = [] } = useModels(provider.id, provider.has_key);
  return (
    <datalist id={`models-${provider.id}`}>
      {data.map((m) => <option key={m} value={m} />)}
    </datalist>
  );
}

function EntryRow({ entry, providers, onChange, onMove, onRemove, canUp, canDown }: {
  entry: RouteEntry;
  providers: ProviderInfo[];
  onChange: (e: RouteEntry) => void;
  onMove: (dir: -1 | 1) => void;
  onRemove: () => void;
  canUp: boolean;
  canDown: boolean;
}) {
  const [testing, setTesting] = useState(false);
  const test = async () => {
    setTesting(true);
    try {
      const r = await api<ProviderTestResult>("/api/providers/test", { method: "POST", body: JSON.stringify(entry) });
      if (r.ok) toast(`${entry.provider}:${entry.model} replied in ${r.latency_ms} ms`);
      else toast.error(r.error ?? "Test failed");
    } finally {
      setTesting(false);
    }
  };
  return (
    <div className="flex items-center gap-2 py-2">
      <select
        aria-label="Provider"
        value={entry.provider}
        onChange={(e) => onChange({ ...entry, provider: e.target.value as ProviderId })}
        className="h-9 rounded-lg border border-hairline bg-paper px-2 text-[13px] text-ink"
      >
        {providers.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
      </select>
      <input
        aria-label="Model"
        list={`models-${entry.provider}`}
        value={entry.model}
        onChange={(e) => onChange({ ...entry, model: e.target.value })}
        className={`${inputClass} w-64`}
      />
      <Button onClick={test} disabled={testing || !entry.model.trim()}>{testing ? "Testing…" : "Test"}</Button>
      <IconButton label="Move up" disabled={!canUp} onClick={() => onMove(-1)}><ArrowUp size={14} /></IconButton>
      <IconButton label="Move down" disabled={!canDown} onClick={() => onMove(1)}><ArrowDown size={14} /></IconButton>
      <IconButton label="Remove" onClick={onRemove}><X size={14} /></IconButton>
    </div>
  );
}

function RoleEditor({ role, label, hint, initial, providers }: {
  role: string; label: string; hint: string; initial: RouteEntry[]; providers: ProviderInfo[];
}) {
  const [draft, setDraft] = useState<RouteEntry[]>(initial);
  const update = useUpdateSettings();
  useEffect(() => setDraft(initial), [initial]);

  const move = (i: number, dir: -1 | 1) =>
    setDraft((d) => {
      const next = [...d];
      [next[i], next[i + dir]] = [next[i + dir], next[i]];
      return next;
    });

  return (
    <div data-testid={`role-${role}`} className="py-4">
      <p className="text-[13.5px] font-medium text-ink">{label}</p>
      <p className="mb-2 text-[12.5px] text-muted">{hint} The first model answers; the others take over if it's unavailable.</p>
      {draft.map((entry, i) => (
        <EntryRow
          key={i}
          entry={entry}
          providers={providers}
          onChange={(e) => setDraft((d) => d.map((x, j) => (j === i ? e : x)))}
          onMove={(dir) => move(i, dir)}
          onRemove={() => setDraft((d) => d.filter((_, j) => j !== i))}
          canUp={i > 0}
          canDown={i < draft.length - 1}
        />
      ))}
      <div className="mt-2 flex gap-2">
        <Button onClick={() => setDraft((d) => [...d, { provider: "local", model: "local" }])}>
          <Plus size={14} /> Add fallback
        </Button>
        <Button
          variant="primary"
          aria-label={`Save ${role} models`}
          disabled={draft.some((e) => !e.model.trim())}
          onClick={() => update.mutate({ roles: { [role]: draft.map((e) => ({ ...e, model: e.model.trim() })) } }, { onSuccess: () => toast(`${label} models saved`) })}
        >
          Save
        </Button>
      </div>
    </div>
  );
}

export function RolesSection({ settings }: { settings: AppSettings }) {
  const { data: providers = [] } = useProviders();
  return (
    <Section title="Models" description="Choose which model does each job, with fallbacks in order.">
      {providers.map((p) => <ModelOptions key={p.id} provider={p} />)}
      {ROLES.map((r) => (
        <RoleEditor key={r.id} role={r.id} label={r.label} hint={r.hint} initial={settings.roles[r.id] ?? NO_ENTRIES} providers={providers} />
      ))}
    </Section>
  );
}
```

`frontend_app/src/features/settings/LocalModelSection.tsx`:
```tsx
import { useState, type ChangeEvent } from "react";
import { toast } from "sonner";
import type { AppSettings, LocalLLMSettings } from "../../lib/types";
import { Button } from "../../ui/Button";
import { Field, inputClass } from "../../ui/Field";
import { Section } from "./Section";
import { useUpdateSettings } from "./useSettings";

export function LocalModelSection({ settings }: { settings: AppSettings }) {
  const [local, setLocal] = useState<LocalLLMSettings>(settings.local_llm);
  const update = useUpdateSettings();
  const num = (key: keyof LocalLLMSettings) => (e: ChangeEvent<HTMLInputElement>) =>
    setLocal((l) => ({ ...l, [key]: Number(e.target.value) || 0 }));

  return (
    <Section title="Local model" description="Used in private mode and as a last-resort fallback. Put a .gguf file in models/llm, or point to one anywhere.">
      <Field label="Model file" hint="Leave empty to use the largest .gguf in models/llm." htmlFor="local-path">
        <input id="local-path" className={inputClass} value={local.model_path} placeholder={"C:\\models\\my-model.gguf"} onChange={(e) => setLocal((l) => ({ ...l, model_path: e.target.value }))} />
      </Field>
      <Field label="Context size" hint="0 uses the model's own trained context." htmlFor="local-ctx">
        <input id="local-ctx" type="number" min={0} className={`${inputClass} w-28`} value={local.context_size} onChange={num("context_size")} />
      </Field>
      <Field label="CPU threads" htmlFor="local-threads">
        <input id="local-threads" type="number" min={1} className={`${inputClass} w-28`} value={local.threads} onChange={num("threads")} />
      </Field>
      <Field label="GPU layers" hint="99 puts everything on the GPU; 0 runs on CPU." htmlFor="local-gpu">
        <input id="local-gpu" type="number" min={0} className={`${inputClass} w-28`} value={local.gpu_layers} onChange={num("gpu_layers")} />
      </Field>
      <div className="py-4">
        <Button variant="primary" onClick={() => update.mutate({ local_llm: local }, { onSuccess: () => toast("Local model settings saved. They apply the next time it starts.") })}>
          Save
        </Button>
      </div>
    </Section>
  );
}
```

`frontend_app/src/features/settings/AppearanceSection.tsx`:
```tsx
import { useUi, type MotionPref, type ThemePref } from "../../stores/ui";
import { Field } from "../../ui/Field";
import { cn } from "../../ui/cn";
import { Section } from "./Section";

function Segmented<T extends string>({ label, value, options, onChange }: {
  label: string; value: T; options: { value: T; label: string }[]; onChange: (v: T) => void;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex rounded-full border border-hairline bg-paper p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn("rounded-full px-3 py-1 text-[12.5px] text-muted transition-colors", value === o.value && "bg-ink text-canvas")}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function AppearanceSection() {
  const { theme, motion, setTheme, setMotion } = useUi();
  return (
    <Section title="Appearance">
      <Field label="Theme">
        <Segmented<ThemePref> label="Theme" value={theme} onChange={setTheme} options={[
          { value: "light", label: "Paper" }, { value: "dark", label: "Ink" }, { value: "system", label: "System" },
        ]} />
      </Field>
      <Field label="Motion" hint="Reduced keeps fades but removes movement.">
        <Segmented<MotionPref> label="Motion" value={motion} onChange={setMotion} options={[
          { value: "system", label: "System" }, { value: "full", label: "Full" }, { value: "reduced", label: "Reduced" },
        ]} />
      </Field>
    </Section>
  );
}
```

`frontend_app/src/features/settings/SettingsView.tsx` (replacement):
```tsx
import { useSettings } from "./useSettings";
import { ModeSection } from "./ModeSection";
import { ProvidersSection } from "./ProvidersSection";
import { RolesSection } from "./RolesSection";
import { LocalModelSection } from "./LocalModelSection";
import { AppearanceSection } from "./AppearanceSection";

export function SettingsView() {
  const { data: settings, error } = useSettings();
  return (
    <div className="h-full overflow-y-auto bg-paper">
      <div className="mx-auto max-w-3xl px-10 py-10">
        <h1 className="font-display text-[44px] leading-none">Settings</h1>
        {error && <p role="alert" className="mt-4 text-accent">{String(error)}</p>}
        {settings && (
          <>
            <ModeSection settings={settings} />
            <ProvidersSection settings={settings} />
            <RolesSection settings={settings} />
            <LocalModelSection settings={settings} />
            <AppearanceSection />
          </>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run the tests and the build**

Run: `cd frontend_app && pnpm test && pnpm build`
Expected: all pass; build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend_app/src/features/settings
git commit -m "feat(ui): add settings for private mode, provider keys, per-role model chains, local model, appearance" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: Start scripts, docs, and end-to-end verification

**Files:**
- Modify: `scripts/start.ps1` (full replacement), `scripts/start.sh` (full replacement), `README.md` (add a "Running Aethel v2" section at the top of the usage docs)

- [ ] **Step 1: Replace `scripts/start.ps1`**

```powershell
# AETHEL v2: start the desktop app (Windows)
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\start.ps1 [-BackendOnly]
#
# Default: runs the Tauri app, which launches the backend itself and passes it a
# fresh auth token. -BackendOnly: runs just the API on http://127.0.0.1:8765
# with auth disabled (AETHEL_DEV=1), for browser development with `pnpm dev`.

param([switch]$BackendOnly)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if ($BackendOnly) {
    $python = if (Test-Path "$Root\.venv\Scripts\python.exe") { "$Root\.venv\Scripts\python.exe" } else { "py" }
    $pyArgs = if ($python -eq "py") { @("-3.11", "-m", "aethel") } else { @("-m", "aethel") }
    $env:AETHEL_DEV = "1"
    Write-Host "Aethel backend on http://127.0.0.1:8765 (dev mode, auth disabled)" -ForegroundColor Cyan
    Push-Location "$Root\backend"
    try { & $python @pyArgs } finally { Pop-Location }
    exit $LASTEXITCODE
}

Push-Location "$Root\frontend_app"
try { pnpm tauri dev } finally { Pop-Location }
```

- [ ] **Step 2: Replace `scripts/start.sh`**

```bash
#!/usr/bin/env bash
# AETHEL v2: start the desktop app. --backend-only runs just the API (dev, auth off).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "${1:-}" == "--backend-only" ]]; then
  PY="$ROOT/.venv/bin/python"; [[ -x "$PY" ]] || PY="python3"
  cd "$ROOT/backend" && AETHEL_DEV=1 exec "$PY" -m aethel
fi
cd "$ROOT/frontend_app" && exec pnpm tauri dev
```

- [ ] **Step 3: Add the README section**

Insert into `README.md` directly after the project introduction:
```markdown
## Running Aethel v2

**Desktop app:** `powershell -ExecutionPolicy Bypass -File scripts\start.ps1`
The Tauri shell starts the backend automatically (logs: `~/.aethel/logs/backend.log`).
Add an API key in **Settings → Providers** (Groq is the default conversation model).

**Browser development:**
1. `scripts\start.ps1 -BackendOnly` (API on http://127.0.0.1:8765, auth disabled)
2. set a key for the backend process, e.g. `$env:GROQ_API_KEY = "..."` before step 1
3. `cd frontend_app; pnpm dev` and open http://localhost:5173

**Private mode:** put a `.gguf` model in `models/llm/` (or choose one in Settings → Local model) and install
llama.cpp (`scripts/install.ps1`). Everything then runs on this computer.

**Tests:** `cd backend; py -3.11 -m pytest` · `cd frontend_app; pnpm test` · `cd frontend_app/src-tauri; cargo test`

**Changing the WebSocket protocol:** edit `backend/aethel/api/events.py`, then run
`py -3.11 scripts/gen_event_schema.py` and `cd frontend_app; pnpm gen:types`.
```

- [ ] **Step 4: Run every automated check**

Run:
```bash
cd backend && py -3.11 -m pytest
cd ../frontend_app && pnpm gen:types && git diff --exit-code src/lib/events.gen.ts && pnpm test && pnpm build
cd src-tauri && cargo test
```
Expected: all backend tests pass; generated types unchanged; all frontend tests pass; build succeeds; 2 Rust tests pass.

- [ ] **Step 5: Manual end-to-end check (the Phase 0 demo scenario)**

1. `powershell -ExecutionPolicy Bypass -File scripts\start.ps1`. The window titled "Aethel" opens on the "waking up…" screen, then shows the "Good …" greeting.
2. Settings → Providers → paste a Groq key → Save. The dot turns terracotta. Close and reopen the app: the key is still marked saved (Credential Manager).
3. Settings → Models → Conversation → **Test**. A toast shows "replied in N ms".
4. Back in the conversation, type "hi, who are you?" and press Enter. The reply streams in word by word with the ink-settling effect, and the thread gets a title.
5. Send a long request ("write 400 words about the sea") and press **Stop** mid-way. The reply ends with "stopped", and the partial text is kept after reopening the conversation from the threads panel.
6. Toggle the theme (moon icon). Warm charcoal dark mode, with no pure black anywhere.
7. Settings → Mode → Private mode ON, then send a message.
   - With `models/llm` empty, the reply shows the message "No local model found. Put a .gguf file in … models\llm …" and an **Open settings** link.
   - With a GGUF present, it answers locally.
8. Quit the app. Confirm no stray `python.exe` process is left (Task Manager).

- [ ] **Step 6: Commit**

```bash
git add scripts/start.ps1 scripts/start.sh README.md
git commit -m "chore: v2 start scripts and README for the Phase 0 foundation" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
