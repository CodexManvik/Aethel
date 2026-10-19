import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aethel.app import create_app
from aethel.keys import KeyStore
from aethel.providers.catalog import NO_KEY, api_key_for, key_name
from aethel.services import build_services
from aethel.settings import AppSettings
from tests.fakes import FakeLocal, FakeProvider, factory_from


def test_the_old_custom_url_becomes_an_endpoint():
    s = AppSettings.model_validate({"custom_base_url": "http://127.0.0.1:1234/v1/",
                                    "roles": {"chat": [{"provider": "custom", "model": "m"}]}})
    assert [(e.id, e.name, e.base_url) for e in s.endpoints] == [("custom", "Custom", "http://127.0.0.1:1234/v1")]
    assert s.custom_base_url == "http://127.0.0.1:1234/v1"
    # a role that names "custom" with no URL saved keeps working (the endpoint exists, unset)
    s = AppSettings.model_validate({"roles": {"chat": [{"provider": "custom", "model": "m"}]}})
    assert s.endpoint("custom").base_url == ""


@pytest.mark.parametrize("bad", [
    {"endpoints": [{"id": "groq", "name": "x", "base_url": ""}]},                       # a built-in's name
    {"endpoints": [{"id": "LM Studio", "name": "x", "base_url": ""}]},                  # not a slug
    {"endpoints": [{"id": "lm", "name": "x", "base_url": "localhost:1234"}]},           # not http(s)
    {"endpoints": [{"id": "lm", "name": "a", "base_url": ""}, {"id": "lm", "name": "b", "base_url": ""}]},
    {"roles": {"chat": [{"provider": "nowhere", "model": "m"}]}},                     # names no endpoint
])
def test_endpoint_validation(bad):
    with pytest.raises(ValidationError):
        AppSettings.model_validate(bad)


def test_keys_by_endpoint():
    s = AppSettings.model_validate({"endpoints": [{"id": "lmstudio", "name": "LM Studio", "base_url": "http://127.0.0.1:1234/v1"},
                                                  {"id": "vllm", "name": "vLLM box", "base_url": "http://10.0.0.5:8000/v1"}]})
    keys = KeyStore()
    assert key_name("lmstudio") == "endpoint:lmstudio" and key_name("custom") == "custom" and key_name("groq") == "groq"
    assert api_key_for("lmstudio", keys, s) == (NO_KEY, None)
    keys.set_many({"endpoint:vllm": "sk-x"})
    assert api_key_for("vllm", keys, s) == ("sk-x", None)
    assert keys.status()["endpoint:vllm"] is True
    with pytest.raises(ValueError):
        keys.set_many({"endpoint:../evil": "x"})


def _client(provider):
    svc = build_services(provider_factory=factory_from({"lmstudio:qwen": provider, "groq:g": provider}),
                         local_llm=FakeLocal(up=False))
    return TestClient(create_app(svc)), svc


def test_routing_through_an_endpoint_and_removing_it():
    provider = FakeProvider(chunks=["hi from lm studio"])
    client, svc = _client(provider)
    with client:
        ok = client.patch("/api/settings", json={
            "endpoints": [{"id": "lmstudio", "name": "LM Studio", "base_url": "http://127.0.0.1:1234/v1"}],
            "roles": {"chat": [{"provider": "lmstudio", "model": "qwen"}]}})
        assert ok.status_code == 200
        listed = {p["id"]: p for p in client.get("/api/providers").json()}
        assert listed["lmstudio"]["label"] == "LM Studio" and listed["lmstudio"]["endpoint"] is True
        assert listed["lmstudio"]["key_optional"] and listed["lmstudio"]["key_name"] == "endpoint:lmstudio"
        assert "custom" not in listed
        conv = client.post("/api/conversations", json={}).json()
        with client.websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "user_message", "conversation_id": conv["id"], "text": "hello"})
            while (ev := ws.receive_json())["type"] != "message_end":
                pass
        assert svc.messages.list(conv["id"])[-1].content == "hi from lm studio"
        # removing an endpoint a role still uses is refused, and says which role
        refused = client.patch("/api/settings", json={"endpoints": []})
        assert refused.status_code == 422 and "chat role" in refused.text
        assert client.patch("/api/settings", json={"roles": {"chat": [{"provider": "groq", "model": "g"}]},
                                                   "endpoints": []}).status_code == 200


def test_endpoint_models_and_unknown_provider():
    client, _ = _client(FakeProvider())
    with client:
        client.patch("/api/settings", json={"endpoints": [{"id": "lmstudio", "name": "LM Studio",
                                                           "base_url": "http://127.0.0.1:1234/v1"}]})
        assert client.get("/api/providers/nowhere/models").status_code == 404
