import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("eval_identity", Path(__file__).parents[2] / "scripts" / "eval_identity.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def test_fifty_scenes_and_distinct_distractors():
    assert len(ev.SCENES) == 50 and len(set(ev.SCENES)) == 50


def test_calibrate_lets_at_most_five_percent_of_different_faces_through():
    different = [i / 100 for i in range(20)]  # 0.00 … 0.19
    tau = ev.calibrate(different)
    assert sum(s >= tau for s in different) <= 1 and sum(s >= tau - 0.011 for s in different) >= 1
    assert ev.calibrate([]) is None


def test_summary():
    s = ev.summary([0.2, 0.5, 0.6, None], tau=0.4, tau_now=0.45)
    assert s["n"] == 4 and s["no_face"] == 1 and s["p50"] == 0.5
    assert s["below_tau_now"] == 1 / 3 and s["pass_at_calibrated"] == 2 / 3
