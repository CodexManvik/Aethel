"""Models that think before they answer need room for it (roadmap §3, carry-over 3)."""
import pytest

from aethel.context.builder import budget_for
from aethel.keys import KeyStore
from aethel.paths import db_path
from aethel.providers.base import ChatMessage, StreamDone, TextDelta
from aethel.providers.router import RoleRouter
from aethel.settings import AppSettings, RouteEntry, SettingsService
from aethel.store.db import Database
from tests.fakes import FakeLocal, ScriptedProvider, factory_from

pytestmark = pytest.mark.anyio


def test_a_reasoning_entry_defaults_to_off():
    assert RouteEntry(provider="groq", model="g").reasoning is False


def test_the_budget_leaves_room_for_thinking_only_when_an_entry_thinks():
    plain = {"provider": "custom", "model": "m", "context_size": 131072}
    s = AppSettings.model_validate({"roles": {"chat": [plain]}})
    thinks = AppSettings.model_validate({"roles": {"chat": [{**plain, "reasoning": True}]}})
    cap = int(16000 * 0.9)  # the chat role's cap binds first
    assert budget_for(s, "chat", 1024) == cap - 1024
    assert budget_for(thinks, "chat", 1024) == cap - 2048   # 2048 reserved instead of the 1024 reply
    assert budget_for(thinks, "chat", 3000) == cap - 3000   # a bigger reply still wins
    mixed = AppSettings.model_validate({"roles": {"chat": [plain, {**plain, "reasoning": True}]}})
    assert budget_for(mixed, "chat", 1024) == cap - 2048    # any thinking entry in the chain: it may be the one called
    small = AppSettings.model_validate({"roles": {"chat": [{**plain, "context_size": 4096, "reasoning": True}]}})
    assert budget_for(small, "chat", 1024) == int(4096 * 0.9) - 1024  # but never more than a quarter of the context
    only = [RouteEntry(provider="custom", model="m", context_size=131072, reasoning=True)]
    assert budget_for(s, "chat", 1024, capped=False, entries=only) == int(131072 * 0.9) - 2048


async def test_the_router_gives_a_thinking_model_at_least_2048_tokens_to_reply_in():
    db = Database(db_path())
    settings = SettingsService(db)
    settings.update({"max_tokens": 1024, "roles": {"chat": [
        {"provider": "groq", "model": "plain"}, {"provider": "openrouter", "model": "thinker", "reasoning": True}]}})
    keys = KeyStore()
    keys.set_many({"groq": "k", "openrouter": "k"})
    plain = ScriptedProvider([[TextDelta("x"), StreamDone("stop")]] * 2)
    thinker = ScriptedProvider([[TextDelta("x"), StreamDone("stop")]] * 3)
    router = RoleRouter(settings=settings, keys=keys, local=FakeLocal(),
                        factory=factory_from({"groq:plain": plain, "openrouter:thinker": thinker}))

    async def ask(entries, **kw):
        settings.update({"roles": {"chat": entries}})
        async for _ in router.stream("chat", [ChatMessage("user", "hi")], **kw):
            pass

    await ask([{"provider": "groq", "model": "plain"}])
    await ask([{"provider": "openrouter", "model": "thinker", "reasoning": True}])
    await ask([{"provider": "openrouter", "model": "thinker", "reasoning": True}], max_tokens=8192)
    await ask([{"provider": "openrouter", "model": "thinker", "reasoning": True}], max_tokens=300)
    assert plain.max_tokens_seen == [1024]
    assert thinker.max_tokens_seen == [2048, 8192, 2048]   # raised to 2048, a bigger limit left alone
    db.close()
