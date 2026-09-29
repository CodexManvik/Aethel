"""Measure System 1 "judge" checks on backend/aethel/eval/s1_judge.jsonl (spec §5.2, §14).

A judge check fails only when P(yes) is below the threshold, so what matters is
how many false claims it rejects (catch rate) and how many true ones it wrongly
rejects (false fails). Writes ~/.aethel/eval/s1_judge.json. In-sample: the
phrasing and threshold were chosen on this same small set."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from aethel.paths import aethel_home  # noqa: E402
from aethel.runtime.checks import JUDGE_OPTIONS, judge_question  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "backend" / "aethel" / "eval" / "s1_judge.jsonl"


def main() -> None:
    from aethel.settings import System1Settings
    from aethel.system1.laya import LayaModel
    from aethel.system1.service import default_model_dir
    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]
    model = LayaModel.load(default_model_dir())
    ps = [model.system_one({"document": r["document"]}, {"q": {"type": "choice", "instructions": judge_question(r["claim"]),
                                                              "criteria": JUDGE_OPTIONS}})["answers"]["q"]["probabilities"]["yes"]
          for r in rows]
    result = {"n": len(rows), "by_threshold": []}
    for t in (0.1, 0.2, 0.3, 0.5):
        false_claims = [p for p, r in zip(ps, rows) if not r["label"]]
        true_claims = [p for p, r in zip(ps, rows) if r["label"]]
        result["by_threshold"].append({"threshold": t, "catch_rate": sum(p < t for p in false_claims) / len(false_claims),
                                       "false_fail_rate": sum(p < t for p in true_claims) / len(true_claims)})
    result["configured"] = System1Settings().judge_threshold
    out = aethel_home() / "eval" / "s1_judge.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    for row in result["by_threshold"]:
        print(f"t={row['threshold']}: rejects {row['catch_rate']:.0%} of false claims, wrongly fails "
              f"{row['false_fail_rate']:.0%} of true ones")
    print(f"configured threshold: {result['configured']}  (in-sample, n={len(rows)})  wrote {out}")


if __name__ == "__main__":
    main()
