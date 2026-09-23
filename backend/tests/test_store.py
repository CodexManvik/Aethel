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
