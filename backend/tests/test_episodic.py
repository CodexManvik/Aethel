import logging

import pytest

from aethel.memory.episodic import EpisodicIndex, exchange_text
from aethel.paths import db_path
from aethel.store.db import Database
from aethel.store.repos import ConversationRepo, MessageRepo
from tests.conftest import fake_embed


@pytest.fixture
def env(tmp_path):
    db = Database(db_path())
    path = tmp_path / "ep" / "index.tvim"
    yield db, ConversationRepo(db), MessageRepo(db), path
    db.close()


def exchange(convs, msgs, conv_id, user, reply, status="complete", meta=None):
    u = msgs.add(conv_id, "user", user, meta=meta)
    a = msgs.add(conv_id, "assistant", reply, status=status)
    return u, a


def test_exchange_text_caps_each_side():
    t = exchange_text("a" * 2000, "b" * 2000)
    assert t.startswith("User: aaa") and "\nAethel: bbb" in t and len(t) < 2100


def test_add_and_search(env):
    db, convs, msgs, path = env
    c = convs.create()
    anna = exchange(convs, msgs, c.id, "my sister Anna visits in May", "lovely, what will you do?")
    printer = exchange(convs, msgs, c.id, "the printer is broken again", "try turning it off and on")
    idx = EpisodicIndex(db, fake_embed, path)
    for u, a in (anna, printer):
        idx.add_exchange(u, a)
    hits = idx.search("when does Anna visit", k=3, min_score=0.1)
    assert hits[0][0].assistant_message_id == anna[1].id and hits[0][0].text.startswith("User: ")
    assert idx.status() == {"indexed": 2, "exchanges": 2, "rebuilding": False}


def test_add_is_idempotent_and_skips_empty(env):
    db, convs, msgs, path = env
    c = convs.create()
    u, a = exchange(convs, msgs, c.id, "hello", "hi there")
    idx = EpisodicIndex(db, fake_embed, path)
    assert idx.add_exchange(u, a) is not None
    assert idx.add_exchange(u, a) is None
    u2, a2 = exchange(convs, msgs, c.id, "hello", "")
    assert idx.add_exchange(u2, a2) is None


def test_excluded_messages_and_min_score(env):
    db, convs, msgs, path = env
    c = convs.create()
    u, a = exchange(convs, msgs, c.id, "my sister Anna visits in May", "lovely")
    idx = EpisodicIndex(db, fake_embed, path)
    idx.add_exchange(u, a)
    assert idx.search("Anna visits", k=3, min_score=0.1, exclude_messages={a.id}) == []
    assert idx.search("Anna visits", k=3, min_score=0.99) == []


def test_catch_up_indexes_missing_complete_exchanges_only(env):
    db, convs, msgs, path = env
    c = convs.create()
    exchange(convs, msgs, c.id, "one", "first reply")
    exchange(convs, msgs, c.id, "two", "second reply")
    exchange(convs, msgs, c.id, "three", "", status="error")
    exchange(convs, msgs, c.id, "open notepad", "Done.", meta={"task_id": "task_1"})
    idx = EpisodicIndex(db, fake_embed, path)
    assert idx.catch_up() == 2
    assert idx.catch_up() == 0


def test_index_persists_and_rebuilds_if_corrupt(env, caplog):
    db, convs, msgs, path = env
    c = convs.create()
    u, a = exchange(convs, msgs, c.id, "my sister Anna visits in May", "lovely")
    EpisodicIndex(db, fake_embed, path).add_exchange(u, a)
    assert EpisodicIndex(db, fake_embed, path).search("Anna", k=1, min_score=0.1)[0][0].assistant_message_id == a.id
    path.write_bytes(b"junk")
    with caplog.at_level(logging.WARNING):
        hits = EpisodicIndex(db, fake_embed, path).search("Anna", k=1, min_score=0.1)
    assert hits[0][0].assistant_message_id == a.id
    assert any("rebuilding" in r.message for r in caplog.records)


def test_index_missing_rows_is_rebuilt(env):
    db, convs, msgs, path = env
    c = convs.create()
    idx = EpisodicIndex(db, fake_embed, path)
    idx.add_exchange(*exchange(convs, msgs, c.id, "my sister Anna visits in May", "lovely"))
    path.unlink()
    idx2 = EpisodicIndex(db, fake_embed, path)
    assert idx2.search("Anna", k=1, min_score=0.1)


def test_remove_conversation(env):
    db, convs, msgs, path = env
    c1, c2 = convs.create(), convs.create()
    idx = EpisodicIndex(db, fake_embed, path)
    idx.add_exchange(*exchange(convs, msgs, c1.id, "my sister Anna visits in May", "lovely"))
    idx.add_exchange(*exchange(convs, msgs, c2.id, "Anna likes cake", "noted"))
    assert idx.remove_conversation(c1.id) == 1
    hits = idx.search("Anna", k=5, min_score=0.0)
    assert [h[0].conversation_id for h in hits] == [c2.id]
    assert db.query("SELECT count(*) FROM episodes WHERE conversation_id = ?", (c1.id,))[0][0] == 0


def test_rebuild_reports_progress(env):
    db, convs, msgs, path = env
    c = convs.create()
    for i in range(3):
        exchange(convs, msgs, c.id, f"message {i}", f"reply {i}")
    idx = EpisodicIndex(db, fake_embed, path)
    seen = []
    assert idx.rebuild(lambda done, total: seen.append((done, total))) == 3
    assert seen[-1] == (3, 3) and idx.status()["indexed"] == 3


def test_status_counts_what_catch_up_would_index(env):
    db, convs, msgs, path = env
    c, d = convs.create(), convs.create()
    exchange(convs, msgs, c.id, "one", "first reply")
    exchange(convs, msgs, c.id, "two", "", status="error")
    exchange(convs, msgs, c.id, "open notepad", "Done.", meta={"task_id": "t"})
    msgs.add(c.id, "user", "dangling question")
    exchange(convs, msgs, d.id, "three", "third reply")
    idx = EpisodicIndex(db, fake_embed, path)
    assert idx.status()["exchanges"] == len(idx._exchanges()) == 2


def test_stop_ends_catch_up_between_batches(env):
    db, convs, msgs, path = env
    c = convs.create()
    for i in range(3):
        exchange(convs, msgs, c.id, f"message {i}", f"reply {i}")
    idx = EpisodicIndex(db, fake_embed, path)
    idx.stop()
    assert idx.catch_up() == 0


def test_threads_can_index_and_search_at_once(env):
    import threading

    db, convs, msgs, path = env
    c = convs.create()
    pairs = [exchange(convs, msgs, c.id, f"topic {i} question", f"topic {i} answer") for i in range(40)]
    idx = EpisodicIndex(db, fake_embed, path)
    errors = []

    def add(chunk):
        try:
            for u, a in chunk:
                idx.add_exchange(u, a)
                idx.search("topic", k=3, min_score=0.0)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
    threads = [threading.Thread(target=add, args=(pairs[i::4],)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and idx.status()["indexed"] == 40
    assert len(EpisodicIndex(db, fake_embed, path).search("topic", k=50, min_score=0.0)) == 40
