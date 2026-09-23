import pytest

from aethel.keys import KeyStore
from aethel.paths import db_path
from aethel.providers.base import ChatMessage, ProviderError, TextDelta
from aethel.providers.router import NoProviderAvailable, RoleRouter
from aethel.settings import SettingsService
from aethel.store.db import Database
from tests.fakes import FakeLocal, FakeProvider, factory_from, fatal, retryable

pytestmark = pytest.mark.anyio
MSGS = [ChatMessage("user", "hi")]


@pytest.fixture
def settings():
    db = Database(db_path())
    svc = SettingsService(db)
    svc.update({"roles": {"chat": [
        {"provider": "groq", "model": "g"},
        {"provider": "openrouter", "model": "o"},
        {"provider": "local", "model": "local"},
    ]}})
    yield svc
    db.close()


def _keys(**kv):
    store = KeyStore()
    store.set_many(kv)
    return store


async def _collect(router, role="chat", switches=None):
    async def on_switch(sw):
        switches.append(sw)
    text = []
    async for ev in router.stream(role, MSGS, on_switch=on_switch if switches is not None else None):
        if isinstance(ev, TextDelta):
            text.append(ev.text)
    return "".join(text)


async def test_first_provider_answers(settings):
    factory = factory_from({"groq:g": FakeProvider(chunks=["hi", "!"])})
    router = RoleRouter(settings=settings, keys=_keys(groq="kg"), local=FakeLocal(), factory=factory)
    switches = []
    assert await _collect(router, switches=switches) == "hi!"
    assert switches == []
    assert factory.used == [("groq", "g", "kg")]


async def test_retryable_failure_before_tokens_fails_over(settings):
    factory = factory_from({
        "groq:g": FakeProvider(label="groq:g", error=retryable()),
        "openrouter:o": FakeProvider(label="openrouter:o", chunks=["ok"]),
    })
    router = RoleRouter(settings=settings, keys=_keys(groq="a", openrouter="b"), local=FakeLocal(), factory=factory)
    switches = []
    assert await _collect(router, switches=switches) == "ok"
    assert [(s.from_label, s.to_label) for s in switches] == [("groq:g", "openrouter:o")]
    assert "rate limited" in switches[0].reason


async def test_failure_after_tokens_is_raised(settings):
    factory = factory_from({"groq:g": FakeProvider(chunks=["a", "b"], error=retryable(), error_at=1)})
    router = RoleRouter(settings=settings, keys=_keys(groq="a", openrouter="b"), local=FakeLocal(), factory=factory)
    with pytest.raises(ProviderError):
        await _collect(router)


async def test_non_retryable_failure_is_raised(settings):
    factory = factory_from({"groq:g": FakeProvider(error=fatal())})
    router = RoleRouter(settings=settings, keys=_keys(groq="a", openrouter="b"), local=FakeLocal(), factory=factory)
    with pytest.raises(ProviderError, match="bad key"):
        await _collect(router)


async def test_entries_without_keys_are_skipped_silently(settings):
    factory = factory_from({"openrouter:o": FakeProvider(chunks=["x"])})
    router = RoleRouter(settings=settings, keys=_keys(openrouter="b"), local=FakeLocal(), factory=factory)
    switches = []
    assert await _collect(router, switches=switches) == "x"
    assert switches == []


async def test_local_used_last_and_started(settings):
    local = FakeLocal()
    factory = factory_from({"local:local": FakeProvider(chunks=["local!"])})
    router = RoleRouter(settings=settings, keys=_keys(), local=local, factory=factory)
    assert await _collect(router) == "local!"
    assert local.ensure_calls == 1
    assert factory.used == [("local", "local", "sk-local")]


async def test_private_mode_uses_only_local(settings):
    settings.update({"private_mode": True})
    factory = factory_from({"local:local": FakeProvider(chunks=["private"])})
    router = RoleRouter(settings=settings, keys=_keys(groq="a", openrouter="b"), local=FakeLocal(), factory=factory)
    assert await _collect(router) == "private"
    assert [u[0] for u in factory.used] == ["local"]


async def test_private_mode_adds_local_when_chain_has_none(settings):
    settings.update({"private_mode": True, "roles": {"vision": [{"provider": "gemini", "model": "v"}]}})
    router = RoleRouter(settings=settings, keys=_keys(), local=FakeLocal(), factory=factory_from({}))
    assert [e.provider for e in router.chain("vision")] == ["local"]


async def test_everything_failing_raises_no_provider_with_reasons(settings):
    router = RoleRouter(settings=settings, keys=_keys(), local=FakeLocal(fail="no model found"),
                        factory=factory_from({}))
    with pytest.raises(NoProviderAvailable) as exc:
        await _collect(router)
    text = str(exc.value)
    assert "groq:g: no API key" in text and "no model found" in text
