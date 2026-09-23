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


def test_private_mode_blocks_cloud_model_listing_and_tests():
    cloud = FakeProvider(chunks=["ok"], models=["m"])
    client, svc = _client({"groq:_list": cloud, "groq:m": cloud, "local:local": FakeProvider(chunks=["ok"])},
                          local=FakeLocal(up=False))
    svc.keys.set_many({"groq": "k"})
    svc.settings.update({"private_mode": True})
    with client:
        listed = client.get("/api/providers/groq/models")
        assert listed.status_code == 409 and listed.json()["detail"] == "Private mode is on."
        tested = client.post("/api/providers/test", json={"provider": "groq", "model": "m"})
        assert tested.status_code == 409 and tested.json()["detail"] == "Private mode is on."
        # local stays reachable
        assert client.get("/api/providers/local/models").json() == {"models": []}
        assert client.post("/api/providers/test", json={"provider": "local", "model": "local"}).status_code == 200
    assert [used[0] for used in svc.provider_factory.used] == ["local"]  # no cloud provider was built
