import pytest

from aethel.memory.facts import FactStore, valid_scope
from aethel.paths import db_path
from aethel.store.db import Database
from tests.conftest import fake_embed


@pytest.fixture
def store():
    db = Database(db_path())
    yield FactStore(db, fake_embed)
    db.close()


def test_add_update_delete_and_history(store):
    f = store.add("user", "Lives in Leeds", actor="extractor")
    assert store.get(f.id).text == "Lives in Leeds"
    g = store.update(f.id, "Lives in York", actor="user")
    assert g.text == "Lives in York" and g.updated_at >= f.updated_at
    gone = store.delete(f.id, actor="user")
    assert gone.text == "Lives in York" and store.get(f.id) is None
    ops = [(e.op, e.old_text, e.new_text, e.actor) for e in store.history(f.id)]
    assert ops == [("delete", "Lives in York", None, "user"), ("update", "Lives in Leeds", "Lives in York", "user"),
                   ("add", None, "Lives in Leeds", "extractor")]


def test_update_or_delete_unknown_returns_none(store):
    assert store.update("fact_nope", "x", actor="user") is None
    assert store.delete("fact_nope", actor="user") is None


def test_text_is_trimmed_and_capped(store):
    f = store.add("user", "  " + "a" * 400 + "  ", actor="user")
    assert len(f.text) == 300


def test_search_ranks_by_similarity_within_scopes(store):
    store.add("user", "Likes green tea", actor="user")
    store.add("user", "Works as a nurse in Leeds", actor="user")
    store.add("persona:aethel", "We joke about green tea", actor="user")
    hits = store.search("what tea does he like", ["user"], k=5)
    assert hits[0][0].text == "Likes green tea"
    assert all(f.scope == "user" for f, _ in hits)
    assert store.search("green tea", ["user"], k=5, min_score=0.99) == []


def test_search_sees_writes_immediately(store):
    assert store.search("tea", ["user"], k=3) == []
    f = store.add("user", "Likes tea", actor="user")
    assert store.search("tea", ["user"], k=3)[0][0].id == f.id
    store.update(f.id, "Likes coffee", actor="user")
    assert store.search("coffee", ["user"], k=1)[0][0].text == "Likes coffee"
    store.delete(f.id, actor="user")
    assert store.search("coffee", ["user"], k=1) == []


def test_list_filters_by_scope_and_query(store):
    store.add("user", "Likes tea", actor="user")
    store.add("persona:aethel", "Calls me Manny", actor="user")
    assert [f.text for f in store.list(scope="user")] == ["Likes tea"]
    assert [f.text for f in store.list(q="MANNY")] == ["Calls me Manny"]


def test_failed_write_rolls_back(store):
    def boom(texts):
        raise RuntimeError("embedder down")
    store.embed = boom
    with pytest.raises(RuntimeError):
        store.add("user", "x", actor="user")
    assert store.list() == [] and store.db.query("SELECT * FROM fact_events") == []


def test_transaction_rolls_back_on_error(store):
    with pytest.raises(RuntimeError):
        with store.db.transaction():
            store.db.execute("INSERT INTO fact_events (fact_id, op, actor, created_at) VALUES ('f', 'add', 'user', 'x')")
            raise RuntimeError("halfway")
    assert store.db.query("SELECT * FROM fact_events") == []


@pytest.mark.parametrize("scope,ok", [("user", True), ("persona:aethel", True), ("persona:", False), ("admin", False)])
def test_valid_scope(scope, ok):
    assert valid_scope(scope) is ok
