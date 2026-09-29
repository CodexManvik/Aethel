import hashlib
import re

import numpy as np
import pytest

from aethel.memory.rsm import KnowledgeStore, effective_confidence


def fake_embed(texts):
    """Bag of words hashed into 256 dims: shared words mean similar vectors."""
    out = np.zeros((len(texts), 256), dtype=np.float32)
    for i, t in enumerate(texts):
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(tmp_path / "knowledge", fake_embed)


HAIKU = {"title": "Write a haiku in Notepad", "intent": "Write a short poem into Notepad and save it",
         "apps": ["Notepad"], "steps": ["Open Notepad with win_app", "Type the poem", "Press ctrl+s and save"],
         "pitfalls": ["The save dialog needs the full path"]}


def test_skill_round_trip_in_spec_format(store):
    doc, created = store.upsert_skill(HAIKU, "approved")
    assert created and doc["id"] == "skill-write-a-haiku-in-notepad"
    assert doc["apps"] == ["notepad"] and doc["steps"][1] == "Type the poem" and doc["macro"] == "none"
    text = (store.root / "skills" / f"{doc['id']}.md").read_text(encoding="utf-8")
    assert text.startswith("---\n") and "status: approved" in text and "## Steps\n1. Open Notepad" in text


def test_a_rediscovered_skill_is_reinforced_not_duplicated(store):
    first, _ = store.upsert_skill(HAIKU, "approved")
    again, created = store.upsert_skill({**HAIKU, "pitfalls": ["Notepad may restore an old tab"]}, "approved")
    assert not created and again["id"] == first["id"] and len(store.skills()) == 1
    assert again["confidence"] == pytest.approx(0.55)
    assert again["pitfalls"] == ["The save dialog needs the full path", "Notepad may restore an old tab"]
    other, created = store.upsert_skill({"title": "Play a YouTube video in Firefox", "apps": ["firefox"],
                                         "steps": ["Open Firefox", "Search YouTube", "Click the first result"]},
                                        "approved")
    assert created and len(store.skills()) == 2


def test_outcomes_durations_and_auto_deprecation(store):
    doc, _ = store.upsert_skill(HAIKU, "approved")
    store.record_outcome([doc["id"]], True, 48.1)
    store.record_outcome([doc["id"]], True, 12.04)
    d = store.get(doc["id"])
    assert (d["runs"], d["successes"], d["duration_history"], d["avg_duration_s"]) == (2, 2, [48.1, 12.0], 30.1)
    assert d["last_used"] is not None
    bad, _ = store.upsert_skill({"title": "Paint a picture in Paint", "apps": ["mspaint"], "steps": ["Open Paint"]},
                                "approved")
    for _ in range(3):
        store.record_outcome([bad["id"]], False, None)
    assert store.get(bad["id"])["status"] == "deprecated"  # (0+1)/(3+2) = 0.2 < 0.34


def test_retrieval_is_approved_only_and_reward_weighted(store):
    good, _ = store.upsert_skill(HAIKU, "approved")
    store.upsert_skill({"title": "Write a haiku in Word", "apps": ["word"], "steps": ["Open Word", "Type"]},
                       "quarantined")
    assert [d["id"] for d in store.retrieve_skills("write a haiku in notepad")] == [good["id"]]
    store.set_status(good["id"], "deprecated")
    assert store.retrieve_skills("write a haiku in notepad") == []
    assert effective_confidence({"confidence": 0.5, "successes": 0, "runs": 0}) == pytest.approx(0.5)


def test_app_notes_merge_and_retrieve(store):
    store.upsert_note("Notepad", ["Ctrl+S opens Save As for a new file.", "Launch with win_app name=Notepad"])
    note = store.upsert_note("notepad", ["ctrl+s opens save as for a new file", "Tabs restore on reopen"])
    assert note["facts"] == ["Ctrl+S opens Save As for a new file.", "Launch with win_app name=Notepad",
                             "Tabs restore on reopen"]
    assert store.retrieve_notes("open notepad and save a file", min_sim=0.1)[0]["app"] == "notepad"
    edited = store.save_note_body("notepad", "- Only this now")
    assert edited["facts"] == ["Only this now"]


def test_v1_skill_files_are_read_tolerantly(store):
    legacy = store.root / "skills" / "skill_04b3e1a565a7.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("---\nid: skill_04b3e1a565a7\ntype: skill\ntitle: List files\nstatus: quarantined\n"
                      "confidence: 0.5\ntimes_succeeded: 2\ntimes_failed: 1\n---\n\n# List files\n\n"
                      "## When to use\n\nWhen the user needs a listing.\n\n## Steps\n\n1. Use fs.list\n", encoding="utf-8")
    d = store.get("skill_04b3e1a565a7")
    assert (d["runs"], d["successes"], d["intent"], d["steps"]) == (3, 2, "When the user needs a listing.",
                                                                   ["Use fs.list"])
    assert "times_succeeded" in legacy.read_text(encoding="utf-8")  # read, never rewritten
