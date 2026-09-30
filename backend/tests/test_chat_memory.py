from fastapi.testclient import TestClient

from aethel.app import create_app
from aethel.chat.persona import system_prompt
from aethel.context.recipes import EPISODES_HEADER, FACTS_HEADER
from aethel.services import build_services
from tests.fakes import FakeLocal, FakeProvider, factory_from


def _client(providers, keys=None, **settings_patch):
    services = build_services(provider_factory=factory_from(providers), local_llm=FakeLocal(fail="no local model"))
    services.keys.set_many(keys if keys is not None else {"groq": "k"})
    services.settings.update({"roles": {"chat": [{"provider": "groq", "model": "g"}]}, **settings_patch})
    return TestClient(create_app(services)), services


def _turn(ws, conv_id, text):
    ws.send_json({"type": "user_message", "conversation_id": conv_id, "text": text})
    events = []
    while True:
        ev = ws.receive_json()
        events.append(ev)
        if ev["type"] == "message_end":
            return events


def test_facts_reach_the_prompt_and_meta():
    provider = FakeProvider(chunks=["Maybe ", "Pip's ball?"])
    client, svc = _client({"groq:g": provider})
    fact = svc.facts.add("user", "Has a dog called Pip", actor="user")
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            events = _turn(ws, conv["id"], "what should I name my dog's new toy?")
    system = provider.calls[0][0].content
    assert FACTS_HEADER in system and "Has a dog called Pip" in system
    types = [e["type"] for e in events]
    assert types.index("context_used") < types.index("token")
    used = next(e for e in events if e["type"] == "context_used")
    assert used["facts"] == [{"id": fact.id, "text": "Has a dog called Pip"}] and used["episodes"] == []
    reply = svc.messages.list(conv["id"])[-1]
    assert reply.meta["context"]["facts"][0]["text"] == "Has a dog called Pip"
    assert reply.content == "Maybe Pip's ball?"


def test_empty_memory_sends_exactly_the_old_prompt():
    provider = FakeProvider(chunks=["hi"])
    client, _ = _client({"groq:g": provider})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            events = _turn(ws, conv["id"], "hello")
    sent = provider.calls[0]
    assert sent[0].role == "system" and sent[0].content.split("Current local time")[0] == \
        system_prompt().split("Current local time")[0]
    assert [(m.role, m.content) for m in sent[1:]] == [("user", "hello")]
    assert "context_used" not in [e["type"] for e in events]


def test_episode_from_another_conversation_is_recalled():
    provider = FakeProvider(chunks=["ok"])
    client, svc = _client({"groq:g": provider}, memory={"episodic_min_score": 0.1})
    with client:
        a = client.post("/api/conversations", json={}).json()
        b = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            _turn(ws, a["id"], "my sister Anna visits in May")
            events = _turn(ws, b["id"], "when is Anna coming to visit?")
    system = provider.calls[1][0].content
    assert EPISODES_HEADER in system and "Anna visits in May" in system and "<untrusted" in system
    used = next(e for e in events if e["type"] == "context_used")
    assert used["episodes"][0]["conversation_id"] == a["id"]


def test_same_conversation_window_is_not_repeated_as_an_episode():
    provider = FakeProvider(chunks=["ok"])
    client, _ = _client({"groq:g": provider}, memory={"episodic_min_score": 0.0})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            _turn(ws, conv["id"], "my sister Anna visits in May")
            _turn(ws, conv["id"], "Anna again")
    assert EPISODES_HEADER not in provider.calls[1][0].content


def test_extraction_scheduled_after_a_complete_turn_only():
    client, svc = _client({"groq:g": FakeProvider(chunks=["ok"])})
    calls = []
    svc.extractor.schedule = lambda **kw: calls.append(kw)
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            _turn(ws, conv["id"], "I live in Leeds")
    user = svc.messages.list(conv["id"])[0]
    assert calls == [{"conversation_id": conv["id"], "persona_id": "aethel", "user_message_id": user.id}]

    client2, svc2 = _client({}, keys={})
    calls2 = []
    svc2.extractor.schedule = lambda **kw: calls2.append(kw)
    with client2:
        conv2 = client2.post("/api/conversations", json={}).json()
        with client2.websocket_connect("/ws/session") as ws:
            _turn(ws, conv2["id"], "I live in Leeds")
    assert calls2 == []


def test_finished_exchanges_are_indexed():
    client, svc = _client({"groq:g": FakeProvider(chunks=["lovely"])})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            _turn(ws, conv["id"], "my sister Anna visits in May")
    assert svc.episodic.status()["indexed"] == 1


def test_retrieval_failure_does_not_break_the_reply():
    client, svc = _client({"groq:g": FakeProvider(chunks=["fine"])})

    def boom(*a, **k):
        raise RuntimeError("index on fire")
    svc.facts.search = boom
    svc.episodic.search = boom
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            events = _turn(ws, conv["id"], "hello")
    assert events[-1]["status"] == "complete"


def test_delete_conversation_removes_its_episodes():
    client, svc = _client({"groq:g": FakeProvider(chunks=["lovely"])})
    with client:
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            _turn(ws, conv["id"], "my sister Anna visits in May")
        assert client.delete(f"/api/conversations/{conv['id']}").status_code == 204
    assert svc.episodic.search("Anna", k=3, min_score=0.0) == []


def test_budget_drops_episodes_before_the_window():
    provider = FakeProvider(chunks=["ok"])
    # budget = 1024 * 0.9 - 600 = 321 tokens: the persona and the message fit, a ~1000-character episode doesn't
    client, svc = _client({"groq:g": provider}, max_tokens=600,
                          roles={"chat": [{"provider": "groq", "model": "g", "context_size": 1024}]},
                          memory={"episodic_min_score": 0.0})
    with client:
        a = client.post("/api/conversations", json={}).json()
        b = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            _turn(ws, a["id"], "Anna " + "long story " * 300)
            _turn(ws, b["id"], "tell me about Anna")
    last = provider.calls[1]
    assert EPISODES_HEADER not in last[0].content and last[-1].content == "tell me about Anna"
