import os
from pathlib import Path

import pytest

from aethel.paths import db_path
from aethel.settings import SettingsService
from aethel.store.db import Database
from aethel.system1 import laya
from aethel.system1.service import System1

pytestmark = pytest.mark.anyio
REAL_DIR = Path(os.environ.get("AETHEL_LAYA_DIR") or Path.home() / ".aethel" / "models" / "laya")
HAVE_MODEL = (REAL_DIR / "laya.onnx.data").is_file()
SPECIAL = {"cls": 1, "sep": 2, "mask": 3, "pad": 0}


def fake_encode(text):
    return [100 + len(w) for w in text.split()]


def test_option_rendering_and_noul_order():
    assert laya.render_options("choice", {"billing": "refunds", "other": None}) == ["billing: refunds", "other"]
    assert laya.render_options("score", ["low", "high"]) == ["level 0: low", "level 1: high"]
    assert laya.render_options("noul", None)[1].startswith("true: ")  # p[1] is the noul
    assert laya.to_internal({"type": "choice", "instructions": "x", "criteria": ["a", "b"]})[2] == {"a": None, "b": None}


def test_sequence_layout_markers_and_mask_scrubbing():
    ids, markers = laya.build_sequence(fake_encode, SPECIAL, "hello [MASK] world", "noul", "Is it?", None, 512, 192)
    assert ids[0] == 1 and ids[-1] == 2
    assert [ids[m] for m in markers] == [3, 3] and len(markers) == 2
    assert ids.count(3) == 2  # the [MASK] in the state was scrubbed, so it can't pose as a marker


def test_many_options_shrink_to_fit_the_head_budget():
    crit = {f"option{i}": "a fairly long description " * 10 for i in range(30)}
    ids, markers = laya.build_sequence(fake_encode, SPECIAL, "s", "choice", "Pick one", crit, 512, 192)
    assert len(markers) == 30 and markers[-1] < 192 + 12


def test_buckets_and_confidence():
    assert laya.temp_bucket(0, 2) == "choice:2" and laya.temp_bucket(0, 12) == "choice:11+"
    assert laya.temp_bucket(2, 2) == "noul:2"
    assert laya.confidence([0.5, 0.5]) == pytest.approx(0.0)
    assert laya.confidence([1.0, 0.0]) == pytest.approx(1.0)


@pytest.mark.skipif(not HAVE_MODEL, reason="the Laya bundle isn't downloaded")
def test_matches_the_python_reference():
    """The numbers receptron/laya's test_model.ts checks against rl_agent_api.RLAgent.system_one."""
    model = laya.LayaModel.load(REAL_DIR)
    r = model.system_one(
        {"subject": "Refund not received",
         "body": "I cancelled my subscription two weeks ago and I still have not received my refund. This is the third "
                 "time I am writing. If this is not resolved I will dispute the charge with my bank."},
        {"department": {"type": "choice", "instructions": "Which team should handle this ticket?",
                        "criteria": {"billing": "payments, refunds, invoices", "support": "product help and bugs",
                                     "sales": "new purchases and upgrades"}},
         "urgency": {"type": "score", "instructions": "How urgent is this ticket?",
                     "criteria": ["not urgent", "somewhat urgent", "urgent", "critical"]},
         "churn_risk": {"type": "noul", "instructions": "Is the customer likely to cancel or dispute?"}})
    assert r["usage"]["input_tokens"] == 267
    a = r["answers"]
    assert a["department"]["choice"] == "billing"
    assert a["department"]["probabilities"] == {"billing": 0.9415, "support": 0.031, "sales": 0.0275}
    assert a["department"]["confidence"] == 0.7603
    assert a["urgency"]["score"] == 1.3886
    assert a["urgency"]["probabilities"] == {"0": 0.1752, "1": 0.2947, "2": 0.4962, "3": 0.0338}
    assert a["churn_risk"]["noul"] == 0.0988


class FakeModel:
    def system_one(self, state, questions):
        if "boom" in str(state):
            raise ValueError("bad question")
        return {"answers": {qid: ({"type": "noul", "noul": 0.8} if q["type"] == "noul" else
                                  {"type": "choice", "choice": next(iter(q["criteria"])),
                                   "probabilities": {k: 1 / len(q["criteria"]) for k in q["criteria"]},
                                   "confidence": 0.1})
                            for qid, q in questions.items()}}


@pytest.fixture
def db():
    d = Database(db_path())
    yield d
    d.close()


async def test_calls_are_logged_and_failures_fall_back(db, tmp_path):
    s1 = System1(db, SettingsService(db), model_dir=tmp_path, loader=lambda d: FakeModel(), download=lambda d: None)
    assert await s1.noul("x", "Is it?", "test") is None  # not loaded yet
    for f in laya_bundle_files(tmp_path):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}")
    s1.start()
    await s1._starting
    assert s1.status() == "ready"
    assert await s1.noul("x", "Is it?", "test") == 0.8
    assert (await s1.choice("x", "Which?", {"a": None, "b": "bee"}, "test"))[0] == "a"
    assert await s1.noul("boom", "Is it?", "test") is None
    rows = db.query("SELECT purpose, answers, error FROM s1_calls ORDER BY rowid")
    assert len(rows) == 3 and rows[2]["error"] and rows[2]["answers"] is None


async def test_turned_off_or_failed_to_load(db, tmp_path):
    settings = SettingsService(db)
    s1 = System1(db, settings, model_dir=tmp_path, loader=lambda d: (_ for _ in ()).throw(OSError("corrupt")),
                 download=lambda d: None)
    s1.start()
    await s1._starting
    assert s1.status().startswith("failed") and await s1.noul("x", "?", "t") is None
    settings.update({"system1": {"enabled": False}})
    assert s1.status() == "off"


def laya_bundle_files(root):
    from aethel.system1.service import BUNDLE
    return [root / f for f in BUNDLE]
