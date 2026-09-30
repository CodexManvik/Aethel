import pytest

from aethel.chat import router
from aethel.paths import db_path
from aethel.settings import SettingsService
from aethel.store.db import Database

pytestmark = pytest.mark.anyio


class FakeS1:
    def __init__(self, act=None, stop=None):
        self.act, self.stop, self.calls = act, stop, []

    async def ask(self, state, questions, purpose):
        self.calls.append((purpose, state))
        if "act" in questions:
            return None if self.act is None else {"act": {"noul": self.act}}
        if self.stop is None:
            return None
        choice = max(self.stop, key=self.stop.get)
        return {"stop": {"choice": choice, "probabilities": self.stop}}


@pytest.fixture
def settings():
    db = Database(db_path())
    yield SettingsService(db)
    db.close()


async def test_act_threshold_and_the_auto_tasks_switch(settings):
    assert await router.route(FakeS1(act=0.8), settings, "open notepad", None) == "task"
    assert await router.route(FakeS1(act=0.3), settings, "how are you?", None) == "chat"
    settings.update({"system1": {"auto_tasks": False}})
    s1 = FakeS1(act=0.99)
    assert await router.route(s1, settings, "open notepad", None) == "chat" and s1.calls == []  # no call at all


async def test_stop_only_while_a_task_runs_and_only_when_sure(settings):
    sure = {"chat": 0.01, "task": 0.02, "stop_task": 0.97}
    unsure = {"chat": 0.05, "task": 0.3, "stop_task": 0.65}
    assert await router.route(FakeS1(act=0.1, stop=sure), settings, "stop", "Write a haiku") == "stop_task"
    assert await router.route(FakeS1(act=0.1, stop=unsure), settings, "open calculator", "Write a haiku") == "chat"
    s1 = FakeS1(act=0.1, stop=sure)
    await router.route(s1, settings, "stop", None)
    assert [p for p, _ in s1.calls] == ["intent_act"]  # no stop question without a running task
    s1 = FakeS1(act=0.1, stop=sure)
    await router.route(s1, settings, "stop", "Write a haiku")
    assert s1.calls[0] == ("intent_stop", {"message": "stop", "task_in_progress": "Write a haiku"})


async def test_without_system1_everything_is_chat(settings):
    assert await router.route(FakeS1(), settings, "open notepad", "Write a haiku") == "chat"


async def test_a_custom_endpoint_needs_a_url_but_not_a_key(settings):
    from aethel.keys import KeyStore
    from aethel.providers.catalog import NO_KEY, api_key_for
    keys = KeyStore()
    assert api_key_for("custom", keys, settings.get()) == (None, "no custom endpoint URL set (Settings → Providers)")
    settings.update({"custom_base_url": "http://127.0.0.1:1234/v1"})
    assert api_key_for("custom", keys, settings.get()) == (NO_KEY, None)   # local servers take no key
    keys.set_many({"custom": "sk-real"})
    assert api_key_for("custom", keys, settings.get()) == ("sk-real", None)
    assert api_key_for("groq", keys, settings.get()) == (None, "no API key")


def test_utility_chain_is_its_own_models_then_chats(settings):
    from aethel.keys import KeyStore
    from aethel.providers.router import RoleRouter
    router = RoleRouter(settings=settings, keys=KeyStore(), local=None, factory=lambda *a: None)
    settings.update({"roles": {"chat": [{"provider": "groq", "model": "big"}, {"provider": "local", "model": "local"}],
                               "utility": [{"provider": "groq", "model": "small"}]}})
    assert [e.model for e in router.chain("utility")] == ["small", "big", "local"]
    settings.update({"roles": {"utility": []}})
    assert [e.model for e in router.chain("utility")] == ["big", "local"]


def test_settings_saved_before_utility_existed_still_route(settings):
    import json
    from aethel.keys import KeyStore
    from aethel.providers.router import RoleRouter
    old = {"roles": {"chat": [{"provider": "groq", "model": "big"}], "agent": [], "vision": []}}
    settings.db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('app', ?)", (json.dumps(old),))
    router = RoleRouter(settings=settings, keys=KeyStore(), local=None, factory=lambda *a: None)
    assert [e.model for e in router.chain("utility")] == ["big"]


def test_primary_is_the_first_usable_entry(settings):
    from aethel.keys import KeyStore
    from aethel.providers.router import RoleRouter
    keys = KeyStore()
    keys.set_many({"gemini": "k"})
    settings.update({"roles": {"agent": [{"provider": "groq", "model": "nokey"},
                                         {"provider": "gemini", "model": "g"}, {"provider": "local", "model": "local"}]}})
    router = RoleRouter(settings=settings, keys=keys, local=None, factory=lambda *a: None)
    assert [e.model for e in router.primary("agent")] == ["g"]
    settings.update({"roles": {"agent": [{"provider": "groq", "model": "nokey"}]}})
    assert router.primary("agent") == []


def test_private_mode_utility_is_local_only(settings):
    from aethel.keys import KeyStore
    from aethel.providers.router import RoleRouter
    settings.update({"private_mode": True, "roles": {"utility": [{"provider": "groq", "model": "small"}],
                                                     "chat": [{"provider": "groq", "model": "big"}]}})
    router = RoleRouter(settings=settings, keys=KeyStore(), local=None, factory=lambda *a: None)
    assert [(e.provider, e.model) for e in router.chain("utility")] == [("local", "local")]
