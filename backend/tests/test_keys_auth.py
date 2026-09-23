import pytest
from fastapi.testclient import TestClient

from aethel.app import create_app
from aethel.auth import AuthConfig
from aethel.keys import KeyStore


def test_keystore_memory_beats_env_and_empty_deletes(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "env-key")
    store = KeyStore()
    assert store.get("groq") == "env-key"
    store.set_many({"groq": "mem-key", "gemini": "g"})
    assert store.get("groq") == "mem-key"
    store.set_many({"groq": None, "gemini": ""})
    assert store.get("groq") == "env-key"
    assert store.get("gemini") is None
    assert store.status() == {"groq": True, "gemini": False, "openrouter": False, "custom": False}


def test_keystore_rejects_unknown_providers():
    with pytest.raises(ValueError):
        KeyStore().set_many({"evil": "x"})


def test_auth_config():
    assert AuthConfig(token=None, dev=True).check(None) is True
    strict = AuthConfig(token="abc", dev=False)
    assert strict.check("abc") is True
    assert strict.check("nope") is False
    assert strict.check(None) is False


def test_auth_from_env_generates_token_when_not_dev(monkeypatch):
    monkeypatch.setenv("AETHEL_DEV", "0")
    cfg = AuthConfig.from_env()
    assert cfg.dev is False and cfg.token and len(cfg.token) >= 32


def test_routes_require_bearer_token(monkeypatch):
    monkeypatch.setenv("AETHEL_DEV", "0")
    monkeypatch.setenv("AETHEL_TOKEN", "t0k")
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/keys/status").status_code == 401
        ok = client.get("/api/keys/status", headers={"Authorization": "Bearer t0k"})
        assert ok.status_code == 200


def test_keys_route_sets_and_reports_status_without_leaking_values():
    with TestClient(create_app()) as client:
        res = client.post("/api/keys", json={"keys": {"groq": "secret-value"}})
        assert res.status_code == 200
        assert res.json()["groq"] is True
        assert "secret-value" not in res.text
        assert client.get("/api/keys/status").json()["groq"] is True
        assert client.post("/api/keys", json={"keys": {"evil": "x"}}).status_code == 422
