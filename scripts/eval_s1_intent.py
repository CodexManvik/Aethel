"""Measure System 1 intent routing on backend/aethel/eval/s1_intent.jsonl (spec §5.3, §14).

Two questions (router.ACT, a yes/no; router.STOP, a choice). Thresholds are chosen on the
dev half (even rows) as the lowest one whose dev precision is 1.0 with recall
>= 0.5, then reported on the held-out test half (odd rows). Writes the result
to ~/.aethel/eval/s1_intent.json. Usage: py -3.11 scripts/eval_s1_intent.py"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from aethel.chat import router  # noqa: E402
from aethel.paths import aethel_home  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "backend" / "aethel" / "eval" / "s1_intent.jsonl"
THRESHOLDS = [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95]
RUNNING = "Write a haiku about rain in Notepad"


def ece(probs: list[float], labels: list[bool], bins: int = 10) -> float:
    """Expected calibration error of P(yes) against the true yes/no labels."""
    total = 0.0
    for b in range(bins):
        idx = [i for i, p in enumerate(probs) if b / bins < p <= (b + 1) / bins or (b == 0 and p == 0)]
        if idx:
            total += len(idx) / len(probs) * abs(sum(labels[i] for i in idx) / len(idx)
                                                 - sum(probs[i] for i in idx) / len(idx))
    return total


def at(probs: list[float], labels: list[bool], t: float) -> dict:
    fired = [i for i, p in enumerate(probs) if p >= t]
    hits = sum(labels[i] for i in fired)
    positives = sum(labels)
    return {"threshold": t, "precision": hits / len(fired) if fired else None,
            "recall": hits / positives if positives else None, "fired": len(fired)}


def pick(probs: list[float], labels: list[bool]) -> float | None:
    for t in THRESHOLDS:
        m = at(probs, labels, t)
        if m["precision"] == 1.0 and m["recall"] >= 0.5:
            return t
    return None


def evaluate(probs: list[float], labels: list[bool]) -> dict:
    dev = [i for i in range(len(probs)) if i % 2 == 0]
    test = [i for i in range(len(probs)) if i % 2 == 1]
    sub = lambda idx, xs: [xs[i] for i in idx]  # noqa: E731
    t = pick(sub(dev, probs), sub(dev, labels))
    return {"chosen_threshold": t,
            "dev": [at(sub(dev, probs), sub(dev, labels), x) for x in THRESHOLDS],
            "test_at_chosen": at(sub(test, probs), sub(test, labels), t) if t is not None else None,
            "test_accuracy_at_0.5": sum((p >= 0.5) == y for p, y in zip(sub(test, probs), sub(test, labels))) / len(test),
            "ece_all": ece(probs, labels)}


def main() -> None:
    from aethel.system1.laya import LayaModel
    from aethel.system1.service import default_model_dir
    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]
    model = LayaModel.load(default_model_dir())
    t0 = time.perf_counter()
    act, stop = [], []
    for r in rows:
        # every row is also asked with a task running, so "stop" sees chat and task messages as negatives
        a = model.system_one(router.act_state(r["text"]), {"act": {"type": "noul", "instructions": router.ACT}})
        act.append(a["answers"]["act"]["noul"])
        s = model.system_one(router.stop_state(r["text"], RUNNING), {"stop": router.STOP})["answers"]["stop"]
        stop.append(s["probabilities"]["stop_task"] if s["choice"] == "stop_task" else 0.0)
    ms = (time.perf_counter() - t0) * 1000 / len(rows)
    not_stop = [i for i, r in enumerate(rows) if r["label"] != "stop_task"]
    result = {"n": len(rows), "ms_per_call": ms, "model": "laya (receptron/laya-onnx@68f27df)",
              "act": evaluate([act[i] for i in not_stop], [rows[i]["label"] == "task" for i in not_stop]),
              "stop": evaluate(stop, [r["label"] == "stop_task" for r in rows])}
    out = aethel_home() / "eval" / "s1_intent.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"n={len(rows)}  {ms:.0f} ms per message (both calls)")
    for name in ("act", "stop"):
        e = result[name]
        print(f"{name}: chosen on dev t={e['chosen_threshold']}  held-out: {e['test_at_chosen']}  "
              f"held-out acc@0.5={e['test_accuracy_at_0.5']:.2f}  ECE={e['ece_all']:.3f}")
    from aethel.settings import System1Settings
    configured = System1Settings()
    for name, t, probs, labels in (
            ("act", configured.intent_threshold, [act[i] for i in not_stop], [rows[i]["label"] == "task" for i in not_stop]),
            ("stop", configured.stop_threshold, stop, [r["label"] == "stop_task" for r in rows])):
        m = at(probs, labels, t)
        result[name]["configured"] = m
        print(f"{name} at the configured threshold {t} (all rows, in-sample): "
              f"precision={m['precision']} recall={m['recall']}")
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
