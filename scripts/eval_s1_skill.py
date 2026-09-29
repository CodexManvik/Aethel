"""Measure System 1 skill selection on backend/aethel/eval/s1_skill.jsonl (spec §5.2, §14).

The question is exactly runtime/recall.py's. A pick counts when it isn't "none"
and its probability clears the threshold. What matters: precision (a confident
wrong skill misleads the task, and can trigger the wrong macro) and coverage
(how often the right skill is picked). The threshold is chosen on the dev half
(even rows) as the lowest with dev precision 1.0 and coverage >= 0.5, and
reported on the held-out odd rows. Writes ~/.aethel/eval/s1_skill.json."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from aethel.paths import aethel_home  # noqa: E402
from aethel.runtime.recall import INSTRUCTIONS, NONE  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "backend" / "aethel" / "eval" / "s1_skill.jsonl"
THRESHOLDS = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]


def at(rows: list[dict], picks: list[tuple[str, float]], t: float) -> dict:
    fired = [(r, c) for r, (c, p) in zip(rows, picks) if c != "none" and p >= t]
    right = sum(1 for r, c in fired if c == r["label"])
    with_label = sum(1 for r in rows if r["label"])
    return {"threshold": t, "precision": right / len(fired) if fired else None,
            "coverage": right / with_label if with_label else None, "picked": len(fired),
            "wrong_picks": len(fired) - right}


def choose(rows: list[dict], picks: list[tuple[str, float]]) -> float | None:
    for t in THRESHOLDS:
        m = at(rows, picks, t)
        if m["precision"] == 1.0 and m["coverage"] >= 0.5:
            return t
    return None


def main() -> None:
    from aethel.system1.laya import LayaModel
    from aethel.system1.service import default_model_dir
    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]
    model = LayaModel.load(default_model_dir())
    picks = []
    for r in rows:
        a = model.system_one({"goal": r["goal"]}, {"q": {"type": "choice", "instructions": INSTRUCTIONS,
                                                          "criteria": {**r["candidates"], "none": NONE}}})["answers"]["q"]
        picks.append((a["choice"], a["probabilities"][a["choice"]]))
    dev, test = rows[0::2], rows[1::2]
    t = choose(dev, picks[0::2])
    result = {"n": len(rows), "chosen_on_dev": t, "dev": [at(dev, picks[0::2], x) for x in THRESHOLDS],
              "held_out_at_chosen": at(test, picks[1::2], t) if t is not None else None,
              "all_rows": [at(rows, picks, x) for x in THRESHOLDS],
              "misses": [{"goal": r["goal"], "label": r["label"], "got": c, "p": round(p, 3)}
                         for r, (c, p) in zip(rows, picks) if c != (r["label"] or "none")]}
    from aethel.settings import System1Settings
    bar = System1Settings().skill_none_threshold
    nones = [(r, p) for r, (c, p) in zip(rows, picks) if c == "none"]
    result["none_bar"] = {"threshold": bar,
                          "hid_hints_correctly": sum(1 for r, p in nones if p >= bar and not r["label"]),
                          "no_fit_goals": sum(1 for r in rows if not r["label"]),
                          "hid_a_fitting_skill": sum(1 for r, p in nones if p >= bar and r["label"])}
    out = aethel_home() / "eval" / "s1_skill.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    for m in result["all_rows"]:
        print(f"t={m['threshold']}: precision={m['precision']} coverage={m['coverage']} wrong picks={m['wrong_picks']}")
    print(f"chosen on dev: {t}; held out: {result['held_out_at_chosen']}")
    nb = result["none_bar"]
    print(f"'none' at >= {nb['threshold']} hides hints for {nb['hid_hints_correctly']} of {nb['no_fit_goals']} no-fit "
          f"goals and wrongly for {nb['hid_a_fitting_skill']} (in-sample)")
    for miss in result["misses"]:
        print(f"  miss: {miss}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
