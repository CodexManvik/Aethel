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
