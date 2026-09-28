import json

from pydantic import BaseModel

from ..protocol import TaskStateName
from ..store.db import Database
from ..store.repos import new_id, now_iso

ACTIVE_STATES = ("planning", "running", "waiting_approval", "verifying")
TERMINAL_STATES = ("done", "failed", "cancelled")


class TaskRecord(BaseModel):
    id: str
    conversation_id: str
    goal: str
    state: TaskStateName
    plan: list[str]
    plan_done: list[int]
    checks: list[dict]
    summary: str | None
    error: str | None
    created_at: str
    updated_at: str
    active_seconds: float = 0.0


class StepRecord(BaseModel):
    id: str
    task_id: str
    idx: int
    tool: str
    args: dict
    summary: str
    verdict: str
    ok: bool | None
    result: str | None
    duration_ms: int | None
    created_at: str
    untrusted: bool = False


def _task(row) -> TaskRecord:
    d = dict(row)
    for key in ("plan", "plan_done", "checks"):
        d[key] = json.loads(d[key])
    return TaskRecord(**d)


def _step(row) -> StepRecord:
    d = dict(row)
    d["args"] = json.loads(d["args"])
    d["ok"] = None if d["ok"] is None else bool(d["ok"])
    d["untrusted"] = bool(d.get("untrusted"))
    return StepRecord(**d)


class TaskRepo:
    def __init__(self, db: Database):
        self.db = db

    def create(self, conversation_id: str, goal: str) -> TaskRecord:
        task_id, ts = new_id("task"), now_iso()
        self.db.execute(
            "INSERT INTO tasks (id, conversation_id, goal, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, conversation_id, goal, "planning", ts, ts),
        )
        return self.get(task_id)

    def get(self, task_id: str) -> TaskRecord | None:
        row = self.db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return _task(row) if row else None

    def list_for_conversation(self, conversation_id: str) -> list[TaskRecord]:
        rows = self.db.query("SELECT * FROM tasks WHERE conversation_id = ? ORDER BY rowid", (conversation_id,))
        return [_task(r) for r in rows]

    def set_state(self, task_id: str, state: str, *, summary: str | None = None, error: str | None = None) -> None:
        placeholders = ",".join("?" for _ in TERMINAL_STATES)
        self.db.execute(
            "UPDATE tasks SET state = ?, summary = COALESCE(?, summary), error = COALESCE(?, error),"
            f" updated_at = ? WHERE id = ? AND state NOT IN ({placeholders})",
            (state, summary, error, now_iso(), task_id, *TERMINAL_STATES),
        )

    def add_active_seconds(self, task_id: str, seconds: float) -> None:
        self.db.execute(
            "UPDATE tasks SET active_seconds = active_seconds + ?, updated_at = ? WHERE id = ?",
            (seconds, now_iso(), task_id),
        )

    def set_plan(self, task_id: str, steps: list[str], checks: list[dict]) -> None:
        self.db.execute(
            "UPDATE tasks SET plan = ?, checks = ?, updated_at = ? WHERE id = ?",
            (json.dumps(steps), json.dumps(checks), now_iso(), task_id),
        )

    def mark_plan_step(self, task_id: str, index: int) -> bool:
        task = self.get(task_id)
        if task is None or not 0 <= index < len(task.plan):
            return False
        if index not in task.plan_done:
            done = sorted(task.plan_done + [index])
            self.db.execute("UPDATE tasks SET plan_done = ?, updated_at = ? WHERE id = ?",
                            (json.dumps(done), now_iso(), task_id))
        return True

    def add_step(self, task_id: str, tool: str, args: dict, summary: str, verdict: str) -> StepRecord:
        step_id = new_id("step")
        idx = self.db.query_one("SELECT COUNT(*) AS n FROM steps WHERE task_id = ?", (task_id,))["n"]
        self.db.execute(
            "INSERT INTO steps (id, task_id, idx, tool, args, summary, verdict, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (step_id, task_id, idx, tool, json.dumps(args), summary, verdict, now_iso()),
        )
        return _step(self.db.query_one("SELECT * FROM steps WHERE id = ?", (step_id,)))

    def finish_step(self, step_id: str, ok: bool, result: str, duration_ms: int, untrusted: bool = False) -> None:
        self.db.execute("UPDATE steps SET ok = ?, result = ?, duration_ms = ?, untrusted = ? WHERE id = ?",
                        (int(ok), result, duration_ms, int(untrusted), step_id))

    def steps(self, task_id: str) -> list[StepRecord]:
        return [_step(r) for r in self.db.query("SELECT * FROM steps WHERE task_id = ? ORDER BY idx", (task_id,))]

    def reconcile_interrupted(self) -> int:
        """At startup nothing is running: tasks cut off mid-flight become
        paused so the user can resume or cancel them."""
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        return self.db.execute(
            f"UPDATE tasks SET state = 'paused', updated_at = ? WHERE state IN ({placeholders})",
            (now_iso(), *ACTIVE_STATES),
        ).rowcount
