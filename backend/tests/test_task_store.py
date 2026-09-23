import pytest

from aethel.paths import db_path
from aethel.runtime.store import TaskRepo
from aethel.store.db import Database
from aethel.store.repos import ConversationRepo


@pytest.fixture
def repo():
    db = Database(db_path())
    yield TaskRepo(db), ConversationRepo(db)
    db.close()


def test_task_lifecycle_and_steps(repo):
    tasks, convs = repo
    conv = convs.create()
    t = tasks.create(conv.id, "write a haiku")
    assert (t.state, t.plan, t.checks) == ("planning", [], [])
    tasks.set_plan(t.id, ["write it", "save it"], [{"kind": "file_exists", "path": "C:/x.txt"}])
    assert tasks.mark_plan_step(t.id, 1) is True
    assert tasks.mark_plan_step(t.id, 1) is True       # idempotent
    assert tasks.mark_plan_step(t.id, 5) is False      # out of range
    s1 = tasks.add_step(t.id, "fs_write", {"path": "C:/x.txt"}, "C:/x.txt", "allow")
    s2 = tasks.add_step(t.id, "fs_read", {"path": "C:/x.txt"}, "C:/x.txt", "allow")
    tasks.finish_step(s1.id, True, "Wrote 5 characters", 12)
    got = tasks.steps(t.id)
    assert [(s.idx, s.tool, s.ok, s.duration_ms) for s in got] == [(0, "fs_write", True, 12), (1, "fs_read", None, None)]
    assert got[0].args == {"path": "C:/x.txt"}
    tasks.set_state(t.id, "done", summary="All set.")
    fresh = tasks.get(t.id)
    assert (fresh.state, fresh.summary, fresh.plan_done) == ("done", "All set.", [1])
    assert [x.id for x in tasks.list_for_conversation(conv.id)] == [t.id]


def test_reconcile_interrupted_pauses_active_tasks(repo):
    tasks, convs = repo
    conv = convs.create()
    running = tasks.create(conv.id, "a")
    tasks.set_state(running.id, "waiting_approval")
    finished = tasks.create(conv.id, "b")
    tasks.set_state(finished.id, "done")
    assert tasks.reconcile_interrupted() == 1
    assert tasks.get(running.id).state == "paused"
    assert tasks.get(finished.id).state == "done"


def test_tasks_are_deleted_with_their_conversation(repo):
    tasks, convs = repo
    conv = convs.create()
    t = tasks.create(conv.id, "a")
    tasks.add_step(t.id, "fs_read", {}, "x", "allow")
    convs.delete(conv.id)
    assert tasks.get(t.id) is None and tasks.steps(t.id) == []
