"""Measure the fact gate (Phase 3 spec §2.3, §8) on backend/aethel/eval/s1_fact.jsonl.

System 1 answers "is this message worth remembering?" for each row. A missed fact loses a memory; a false
alarm only costs one LLM call that usually returns no operations. So the threshold is chosen on the dev
half (even rows) as the HIGHEST one whose dev recall is at least 0.9, then reported on the held-out test
half (odd rows). Alternative wordings of the question are compared on the dev half only; the test half is
scored once, for the chosen wording. Writes ~/.aethel/eval/s1_fact.json.
Usage: py -3.11 scripts/eval_s1_fact.py"""
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from aethel.memory.extract import FACT_Q, gate_state  # noqa: E402
from aethel.paths import aethel_home  # noqa: E402

_spec = importlib.util.spec_from_file_location("eval_s1_intent", ROOT / "scripts" / "eval_s1_intent.py")
_intent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_intent)
at, ece = _intent.at, _intent.ece

DATA = ROOT / "backend" / "aethel" / "eval" / "s1_fact.jsonl"
THRESHOLDS = [0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
MIN_RECALL = 0.9
WORDINGS = {  # literal, so the run is reproducible whatever extract.FACT_Q says now
    "spec": "Does this message tell Aethel something about the user that is worth remembering in future "
            "conversations, such as their name, life, work, people, plans, likes or dislikes, or a correction "
            "of something Aethel knew? Questions, requests, commands and small talk are not.",
    "personal": "Does this message reveal a personal detail about the user (who they are, their life, people, "
                "plans, tastes or needs) that a friend would remember?",
    "short": "Is the user telling Aethel something about themselves?",
    "memory": "Should Aethel save something from this message to its long-term memory about the user? "
              "Only personal facts count, not questions, tasks or chat.",
}


def pick(probs: list[float], labels: list[bool]) -> float | None:
    """The highest threshold that still catches MIN_RECALL of the facts."""
    ok = [t for t in THRESHOLDS if (at(probs, labels, t)["recall"] or 0) >= MIN_RECALL]
    return max(ok) if ok else None


def split(xs: list) -> tuple[list, list]:
    return xs[0::2], xs[1::2]


def main() -> None:
    from aethel.system1.laya import LayaModel
    from aethel.system1.service import default_model_dir
    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]
    labels = [r["label"] == "fact" for r in rows]
    dev_labels, test_labels = split(labels)
    model = LayaModel.load(default_model_dir())
    by_wording, timing = {}, {}
    for name, question in WORDINGS.items():
        t0 = time.perf_counter()
        probs = [model.system_one(gate_state(r["text"], r["previous_reply"]),
                                  {"fact": {"type": "noul", "instructions": question}})["answers"]["fact"]["noul"]
                 for r in rows]
        timing[name] = (time.perf_counter() - t0) * 1000 / len(rows)
        dev = split(probs)[0]
        t = pick(dev, dev_labels)
        by_wording[name] = {"probs": probs, "threshold": t,
                            "dev_at_threshold": at(dev, dev_labels, t) if t is not None else None,
                            "dev": [at(dev, dev_labels, x) for x in THRESHOLDS]}
        m = by_wording[name]["dev_at_threshold"]
        print(f"[{name}] dev: t={t} {m}  ({timing[name]:.0f} ms/call)")
    usable = {k: v for k, v in by_wording.items() if v["threshold"] is not None}
    if not usable:
        print("No wording reaches the recall target on the dev half; nothing chosen.")
        chosen = None
    else:
        chosen = max(usable, key=lambda k: (usable[k]["dev_at_threshold"]["precision"] or 0, usable[k]["threshold"]))
    result = {"n": len(rows), "model": "laya (receptron/laya-onnx@68f27df)", "min_recall": MIN_RECALL,
              "wordings": {k: {kk: vv for kk, vv in v.items() if kk != "probs"} | {"ms_per_call": timing[k]}
                           for k, v in by_wording.items()},
              "chosen": chosen}
    if chosen is not None:
        w = by_wording[chosen]
        test = split(w["probs"])[1]
        result["test_at_chosen"] = at(test, test_labels, w["threshold"])
        result["ece_all"] = ece(w["probs"], labels)
        result["question"] = WORDINGS[chosen]
        if WORDINGS[chosen] != FACT_Q:
            print("note: extract.FACT_Q differs from the chosen wording")
        print(f"chosen wording: {chosen}, threshold {w['threshold']}")
        print(f"held out: {result['test_at_chosen']}  ECE (all rows) = {result['ece_all']:.3f}")
    out = aethel_home() / "eval" / "s1_fact.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
