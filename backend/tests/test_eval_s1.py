import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "eval_s1_intent", Path(__file__).resolve().parents[2] / "scripts" / "eval_s1_intent.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def test_threshold_metrics_and_pick():
    probs = [0.95, 0.8, 0.6, 0.55, 0.2, 0.1]
    labels = [True, True, False, True, False, False]
    m = ev.at(probs, labels, 0.7)
    assert (m["precision"], m["fired"]) == (1.0, 2) and abs(m["recall"] - 2 / 3) < 1e-9
    assert ev.pick(probs, labels) == 0.7  # 0.5 and 0.6 let the false 0.6 through
    assert ev.at(probs, labels, 0.99)["precision"] is None


def test_ece_is_zero_when_perfectly_calibrated_and_large_when_not():
    assert ev.ece([1.0, 1.0, 0.0], [True, True, False]) == 0.0
    assert ev.ece([0.95] * 4, [False] * 4) > 0.9


def test_skill_selection_metrics():
    spec2 = importlib.util.spec_from_file_location(
        "eval_s1_skill", Path(__file__).resolve().parents[2] / "scripts" / "eval_s1_skill.py")
    sk = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(sk)
    rows = [{"label": "a"}, {"label": "b"}, {"label": None}, {"label": "c"}]
    picks = [("a", 0.9), ("x", 0.4), ("none", 0.99), ("c", 0.35)]
    m = sk.at(rows, picks, 0.3)
    assert (m["picked"], m["wrong_picks"], m["precision"]) == (3, 1, 2 / 3)
    assert sk.at(rows, picks, 0.5) == {"threshold": 0.5, "precision": 1.0, "coverage": 1 / 3, "picked": 1,
                                       "wrong_picks": 0}
    assert sk.choose([rows[0], rows[3]], [picks[0], picks[3]]) == 0.3
