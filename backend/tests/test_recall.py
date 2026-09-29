import pytest

from aethel.memory.rsm import KnowledgeStore
from aethel.runtime.recall import recall
from tests.test_rsm import HAIKU, fake_embed

pytestmark = pytest.mark.anyio


class PickS1:
    def __init__(self, pick, p):
        self.pick, self.p, self.asked = pick, p, []

    async def choice(self, state, instructions, options, purpose):
        self.asked.append(options)
        key = self.pick if self.pick in options else next(iter(options))
        return key, {k: (self.p if k == key else (1 - self.p) / (len(options) - 1)) for k in options}, 0.5


@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(tmp_path / "k", fake_embed)
    s.upsert_skill(HAIKU, "approved")
    s.upsert_skill({"title": "Write a haiku in Word", "intent": "Write a poem in Word", "apps": ["word"],
                    "steps": ["Open Word", "Type the poem"]}, "approved")
    s.upsert_note("notepad", ["Ctrl+S opens Save As for a new file"])
    return s


async def test_a_confident_pick_shows_only_that_skill(store):
    r = await recall(store, PickS1("skill-write-a-haiku-in-notepad", 0.9), "write a haiku in notepad", 0.6)
    assert r.chosen == "skill-write-a-haiku-in-notepad" and r.skill_ids == [r.chosen]
    assert "A skill you learned for this: Write a haiku in Notepad" in r.text and "Word" not in r.text


async def test_unsure_or_no_system1_offers_maybes_and_confident_none_offers_nothing(store):
    unsure = await recall(store, PickS1("skill-write-a-haiku-in-notepad", 0.4), "write a haiku in notepad", 0.6)
    assert unsure.chosen is None and len(unsure.skill_ids) == 2 and "A skill that may help" in unsure.text
    none = await recall(store, PickS1("none", 0.95), "write a haiku in notepad", 0.6)
    assert none.skill_ids == [] and none.chosen is None
    assert await recall(None, None, "anything", 0.6) == type(none)()


async def test_notes_come_along_only_when_relevant(store):
    r = await recall(store, None, "in notepad ctrl+s opens save as for a new file", 0.6)
    assert "### Notes on notepad\n- Ctrl+S opens Save As for a new file" in r.text
    far = await recall(store, None, "play a video on youtube in firefox", 0.6)
    assert far.text is None or "Notes on notepad" not in far.text
