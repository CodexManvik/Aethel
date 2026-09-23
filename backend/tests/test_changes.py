from aethel.paths import db_path
from aethel.safety.changes import ChangeLog
from aethel.store.db import Database


def test_rollback_restores_overwritten_and_removes_created(tmp_path):
    db = Database(db_path())
    log = ChangeLog(db)
    existing = tmp_path / "a.txt"
    existing.write_text("old", encoding="utf-8")
    c1 = log.record_before_write(str(existing), task_id="t1")
    existing.write_text("new", encoding="utf-8")
    created = tmp_path / "b.txt"
    log.record_before_write(str(created), task_id="t1")
    created.write_text("fresh", encoding="utf-8")

    results = log.rollback_task("t1")
    assert all(ok for ok, _ in results)
    assert existing.read_text(encoding="utf-8") == "old"
    assert not created.exists()
    assert log.rollback(c1) == (False, "Already rolled back.")
    assert [c["rolled_back"] for c in log.for_task("t1")] == [True, True]
    db.close()
