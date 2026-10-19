import json

import pytest

from aethel.chat.persona import AETHEL_PERSONA
from aethel.personas.legacy import legacy_rosia, legacy_voice
from aethel.personas.model import Persona, PersonaVoice
from aethel.personas.store import BUILTIN_ID, PersonaStore, slug


@pytest.fixture
def store(tmp_path):
    s = PersonaStore(tmp_path / "personas")
    s.ensure_builtin()
    return s


def test_ensure_builtin_is_idempotent_and_keeps_edits(store):
    builtin = store.get(BUILTIN_ID)
    assert builtin.built_in and builtin.section("Personality") == AETHEL_PERSONA.strip()
    store.update(BUILTIN_ID, {"sections": {"Personality": "Edited."}})
    store.ensure_builtin()
    assert store.get(BUILTIN_ID).section("Personality") == "Edited."
    assert [p.id for p in store.list()] == [BUILTIN_ID]


def test_create_slugs_the_name_and_handles_collisions(store):
    a = store.create(Persona(id="x", name="Mira Lane"))
    b = store.create(Persona(id="x", name="Mira Lane"))
    c = store.create(Persona(id="x", name="Aethel"))
    assert (a.id, b.id, c.id) == ("mira-lane", "mira-lane-2", "aethel-2")
    assert a.created and not c.built_in
    assert [p.id for p in store.list()] == ["aethel", "aethel-2", "mira-lane", "mira-lane-2"]


def test_a_deleted_or_still_used_id_is_never_reused(tmp_path):
    used = {"kit"}
    s = PersonaStore(tmp_path / "p", in_use=lambda pid: pid in used)
    zed = s.create(Persona(id="x", name="Zed"))
    s.delete(zed.id)
    assert s.create(Persona(id="x", name="Zed")).id == "zed-2"  # "zed" is in the trash, its memories may remain
    assert s.create(Persona(id="x", name="Kit")).id == "kit-2"  # a conversation still says "kit"


def test_migration_falls_back_to_the_default_voice(tmp_path):
    s = PersonaStore(tmp_path / "p", known_voices=lambda: ["af_heart"])
    rosia = Persona(id="rosia", name="Rosia", voice=PersonaVoice(blend={"bf_gone": 1.0}))
    assert s.migrate_legacy(rosia, tmp_path / "sentinel") == "rosia"
    assert s.get("rosia").voice.blend == {"af_heart": 1.0}


def test_slug_edge_cases():
    assert slug("  ✨ ") == "persona" and slug("Zoë O'Brien") == "zo-o-brien" and len(slug("a" * 99)) == 40


def test_builtin_cannot_be_deleted_or_renamed(store):
    assert store.delete(BUILTIN_ID) is False
    updated = store.update(BUILTIN_ID, {"name": "Bob", "id": "bob", "image_style": "ink"})
    assert updated.name == "Aethel" and updated.id == BUILTIN_ID and updated.image_style == "ink"


def test_update_merges_sections_and_validates(store):
    p = store.create(Persona(id="x", name="Mira", sections={"Personality": "Curious.", "Backstory": "Sea."}))
    p = store.update(p.id, {"sections": {"Backstory": "Mountains."}, "tone_engine": False})
    assert p.section("Personality") == "Curious." and p.section("Backstory") == "Mountains." and not p.tone_engine
    with pytest.raises(ValueError):
        store.update(p.id, {"image_frequency": "always"})
    with pytest.raises(KeyError):
        store.update("nobody", {})


def test_unknown_voice_rejected_when_voices_are_known(tmp_path):
    s = PersonaStore(tmp_path / "p", known_voices=lambda: ["af_heart"])
    with pytest.raises(ValueError):
        s.create(Persona(id="x", name="Mira", voice=PersonaVoice(blend={"bf_emma": 1.0})))


def test_delete_moves_the_folder_to_the_trash(store):
    p = store.create(Persona(id="x", name="Mira"))
    assert store.delete(p.id) is True
    assert store.get(p.id) is None and store.delete(p.id) is False
    trashed = list((store.root / ".trash").iterdir())
    assert len(trashed) == 1 and (trashed[0] / "persona.md").exists()
    assert [x.id for x in store.list()] == [BUILTIN_ID]


def test_a_broken_file_is_skipped(store):
    (store.root / "broken").mkdir()
    (store.root / "broken" / "persona.md").write_text("---\n[not, a, mapping]\n---\n", encoding="utf-8")
    assert [p.id for p in store.list()] == [BUILTIN_ID] and store.get("broken") is None
    assert store.get("../escape") is None


def test_style_directive(store):
    p = store.create(Persona(id="x", name="Mira"))
    assert store.style_directive(p.id) is None
    store.set_style_directive(p.id, "Writes in short lowercase bursts.")
    assert store.style_directive(p.id) == "Writes in short lowercase bursts."
    store.set_style_directive(p.id, "  ")
    assert store.style_directive(p.id) is None


def test_migration_runs_once_with_legacy_data(store, tmp_path):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / "settings.json").write_text(json.dumps({"kokoro_voice": "bf_emma"}), encoding="utf-8")
    sentinel = tmp_path / ".migrated-personas"
    rosia = legacy_rosia(dirs=[legacy_dir], settings_path=legacy_dir / "settings.json")
    assert rosia.voice.blend == {"bf_emma": 1.0}
    assert store.migrate_legacy(rosia, sentinel) == "rosia"
    assert store.migrate_legacy(rosia, sentinel) is None
    store.delete("rosia")
    assert store.migrate_legacy(rosia, sentinel) is None  # deleted stays deleted
    assert store.get("rosia") is None


def test_migration_never_runs_without_legacy_data(store, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    rosia = legacy_rosia(dirs=[empty, tmp_path / "missing"])
    assert rosia is None
    sentinel = tmp_path / ".migrated-personas"
    assert store.migrate_legacy(rosia, sentinel) is None and not sentinel.exists()


def test_legacy_voice_falls_back(tmp_path):
    assert legacy_voice(tmp_path / "nope.json") == "af_heart"
