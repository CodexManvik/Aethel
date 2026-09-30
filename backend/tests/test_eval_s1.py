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


def _load(name):
    s = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def test_fact_gate_picks_the_highest_threshold_that_keeps_recall():
    fact = _load("eval_s1_fact")
    probs = [0.9, 0.6, 0.35, 0.12, 0.5, 0.08, 0.02]
    labels = [True, True, True, True, False, False, False]
    assert fact.pick(probs, labels) == 0.1   # 0.15 and up miss the 0.12 fact: recall 0.75 < 0.9
    assert fact.pick([0.01, 0.9], [True, False]) is None  # no threshold on the grid catches the fact
    assert fact.split([0, 1, 2, 3, 4]) == ([0, 2, 4], [1, 3])


def test_episodic_floor_scores_like_chat_recall():
    ep = _load("eval_episodic")
    rows = [
        {"expect": ["a:0"], "hits": [["a:1", 0.80], ["a:0", 0.70]]},  # right one second: correct at 0.7
        {"expect": ["b:0"], "hits": [["c:0", 0.75], ["b:0", 0.60]]},  # right one below 0.7: wrong
        {"expect": [], "hits": [["a:0", 0.66]]},                      # nothing relevant exists
    ]
    assert ep.at(rows, 0.70) == {"threshold": 0.70, "correct": 1, "wrong": 1, "unwanted": 0, "recall": 0.5,
                                 "precision": 0.5}
    assert ep.at(rows, 0.60)["unwanted"] == 1 and ep.at(rows, 0.60)["correct"] == 2
    assert ep.at(rows, 0.90)["precision"] is None
    # net correct - wrong - unwanted: 1 up to 0.60 (b:0 still recalled), -1 at 0.61-0.66, 0 at 0.67-0.70 …
    assert ep.choose_floor(rows) == 0.6  # … and ties go to the higher floor
