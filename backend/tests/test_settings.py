import json

import pytest
from pydantic import ValidationError

from aethel.paths import db_path
from aethel.settings import AppSettings, RouteEntry, SettingsService
from aethel.store.db import Database


@pytest.fixture
def service():
    db = Database(db_path())
    yield SettingsService(db)
    db.close()


def test_defaults_have_a_chain_for_each_role(service):
    s = service.get()
    assert set(s.roles) == {"chat", "agent", "vision"}
    assert s.roles["chat"][0] == RouteEntry(provider="groq", model="llama-3.3-70b-versatile")
    assert s.roles["chat"][-1].provider == "local"
    assert s.private_mode is False
    assert s.local_llm.context_size == 0


def test_update_deep_merges_and_persists(service):
    service.update({"private_mode": True, "local_llm": {"threads": 8}})
    fresh = service.get()
    assert fresh.private_mode is True
    assert fresh.local_llm.threads == 8
    assert fresh.local_llm.gpu_layers == 99


def test_update_replaces_role_lists_wholesale(service):
    service.update({"roles": {"chat": [{"provider": "gemini", "model": "gemini-2.5-flash"}]}})
    s = service.get()
    assert s.roles["chat"] == [RouteEntry(provider="gemini", model="gemini-2.5-flash")]
    assert s.roles["agent"]  # other roles untouched


def test_invalid_provider_is_rejected_and_nothing_saved(service):
    with pytest.raises(ValidationError):
        service.update({"roles": {"chat": [{"provider": "nope", "model": "x"}]}})
    assert service.get() == AppSettings()


def test_legacy_import_runs_once(service, tmp_path):
    legacy = tmp_path / "settings.json"
    legacy.write_text(
        json.dumps({"llm_model_path": "C:/m.gguf", "context_size": 4096, "threads": 6, "gpu_layers": 20}),
        encoding="utf-8",
    )
    assert service.import_legacy(legacy) is True
    local = service.get().local_llm
    assert (local.model_path, local.context_size, local.threads, local.gpu_layers) == ("C:/m.gguf", 4096, 6, 20)
    service.update({"local_llm": {"threads": 2}})
    assert service.import_legacy(legacy) is False
    assert service.get().local_llm.threads == 2


def test_legacy_import_tolerates_missing_file(service, tmp_path):
    assert service.import_legacy(tmp_path / "nope.json") is False
