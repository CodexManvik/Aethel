import threading

from fastapi.testclient import TestClient

from aethel.app import create_app
from aethel.services import build_services
from tests.fakes import FakeLocal, factory_from


def _client():
    svc = build_services(provider_factory=factory_from({}), local_llm=FakeLocal(fail="x"))
    return TestClient(create_app(svc)), svc


def test_fact_crud_round_trip_and_history():
    client, svc = _client()
    with client:
        conv = client.post("/api/conversations", json={}).json()
        client.patch(f"/api/conversations/{conv['id']}", json={"title": "Dogs"})
        f = svc.facts.add("user", "Has a dog called Pip", actor="extractor", conversation_id=conv["id"])
        listed = client.get("/api/memory/facts").json()
        assert [(x["text"], x["conversation_title"], x["added_by"]) for x in listed] == \
            [("Has a dog called Pip", "Dogs", "extractor")]
        added = client.post("/api/memory/facts", json={"scope": "persona:aethel", "text": " Calls me Manny "}).json()
        assert (added["scope"], added["text"], added["conversation_title"], added["added_by"]) == \
            ("persona:aethel", "Calls me Manny", None, "user")
        assert [x["text"] for x in client.get("/api/memory/facts", params={"q": "manny"}).json()] == ["Calls me Manny"]
        assert [x["text"] for x in client.get("/api/memory/facts", params={"scope": "user"}).json()] == \
            ["Has a dog called Pip"]
        edited = client.patch(f"/api/memory/facts/{f.id}", json={"text": "Has two dogs, Pip and Rex"}).json()
        assert edited["text"] == "Has two dogs, Pip and Rex"
        assert client.delete(f"/api/memory/facts/{f.id}").status_code == 204
        assert client.delete(f"/api/memory/facts/{f.id}").status_code == 404
        assert client.patch(f"/api/memory/facts/{f.id}", json={"text": "x"}).status_code == 404
        history = client.get(f"/api/memory/facts/{f.id}/history").json()
        assert [(e["op"], e["actor"]) for e in history] == [("delete", "user"), ("update", "user"),
                                                             ("add", "extractor")]


def test_fact_validation():
    client, _ = _client()
    with client:
        assert client.post("/api/memory/facts", json={"text": ""}).status_code == 422
        assert client.post("/api/memory/facts", json={"text": "   "}).status_code == 422
        assert client.post("/api/memory/facts", json={"text": "x" * 301}).status_code == 422
        assert client.post("/api/memory/facts", json={"scope": "admin", "text": "x"}).status_code == 422


def test_undo_each_kind_of_change():
    client, svc = _client()
    with client:
        conv = svc.conversations.create()
        added = svc.facts.add("user", "Has a dog called Pip", actor="extractor")
        updated = svc.facts.add("user", "Lives in York", actor="extractor")
        changes = [
            {"fact_id": added.id, "op": "add", "scope": "user", "text": "Has a dog called Pip", "old_text": None},
            {"fact_id": updated.id, "op": "update", "scope": "user", "text": "Lives in York", "old_text": "Lives in Leeds"},
            {"fact_id": "fact_gone", "op": "delete", "scope": "user", "text": None, "old_text": "Works at a bank"},
        ]
        msg = svc.messages.add(conv.id, "user", "…", meta={"facts_changed": changes})
        for i in range(3):
            r = client.post("/api/memory/facts/undo", json={"message_id": msg.id, "index": i})
            assert r.status_code == 200 and r.json()[i]["undone"] is True
        assert sorted(f.text for f in svc.facts.list()) == ["Lives in Leeds", "Works at a bank"]
        assert all(c["undone"] for c in svc.messages.get(msg.id).meta["facts_changed"])
        assert client.post("/api/memory/facts/undo", json={"message_id": msg.id, "index": 0}).status_code == 409
        assert client.post("/api/memory/facts/undo", json={"message_id": msg.id, "index": 7}).status_code == 404
        assert client.post("/api/memory/facts/undo", json={"message_id": "msg_x", "index": 0}).status_code == 404


def test_episodic_status_and_rebuild():
    client, svc = _client()
    started, release = threading.Event(), threading.Event()

    def slow_rebuild(on_progress=None):
        svc.episodic._rebuilding = True
        started.set()
        release.wait(5)
        svc.episodic._rebuilding = False
        return 0

    svc.episodic.rebuild = slow_rebuild
    with client:
        assert client.get("/api/memory/episodic").json() == {"indexed": 0, "exchanges": 0, "rebuilding": False}
        assert client.post("/api/memory/episodic/rebuild").status_code == 202
        assert started.wait(5)
        assert client.get("/api/memory/episodic").json()["rebuilding"] is True
        assert client.post("/api/memory/episodic/rebuild").status_code == 409
        release.set()


def test_undo_refuses_to_clobber_a_newer_edit():
    client, svc = _client()
    with client:
        conv = svc.conversations.create()
        f = svc.facts.add("user", "Lives in York", actor="extractor")
        change = {"fact_id": f.id, "op": "update", "scope": "user", "text": "Lives in York", "old_text": "Lives in Leeds"}
        msg = svc.messages.add(conv.id, "user", "…", meta={"facts_changed": [change]})
        svc.facts.update(f.id, "Lives in Hull", actor="user")
        r = client.post("/api/memory/facts/undo", json={"message_id": msg.id, "index": 0})
        assert r.status_code == 409 and svc.facts.get(f.id).text == "Lives in Hull"


def test_concurrent_undo_of_a_delete_adds_the_fact_once():
    from concurrent.futures import ThreadPoolExecutor

    client, svc = _client()
    with client:
        conv = svc.conversations.create()
        change = {"fact_id": "fact_gone", "op": "delete", "scope": "user", "text": None, "old_text": "Works at a bank"}
        msg = svc.messages.add(conv.id, "user", "…", meta={"facts_changed": [change]})
        with ThreadPoolExecutor(4) as pool:
            codes = sorted(pool.map(lambda _: client.post("/api/memory/facts/undo",
                                                          json={"message_id": msg.id, "index": 0}).status_code,
                                    range(4)))
        assert codes == [200, 409, 409, 409]
        assert [f.text for f in svc.facts.list()] == ["Works at a bank"]
