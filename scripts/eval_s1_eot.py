"""Measure System 1's end-of-turn judgment on backend/aethel/eval/s1_eot.jsonl (voice spec §8.3).

100 hand-written call transcripts, half finished thoughts and half cut off mid-sentence. A false "finished" makes
Aethel talk over the user, which is worse than waiting 1.5 s, so the threshold is the LOWEST one whose dev
precision for "finished" is >= 0.9. Wordings (five were tried, a yes/no and a choice; the plan allowed three, but none reached the bar, so
more were tried, always on dev only) and the threshold are chosen on the dev half (even rows) only; the held-out half (odd rows) is reported once. Writes ~/.aethel/eval/s1_eot.json.
Usage: py -3.11 scripts/eval_s1_eot.py"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_s1_intent import at, ece  # noqa: E402

from aethel.paths import aethel_home  # noqa: E402
from aethel.voice.turns import EOT_INSTRUCTIONS, EOT_OPTIONS, eot_state  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "backend" / "aethel" / "eval" / "s1_eot.jsonl"
THRESHOLDS = [round(0.05 * i, 2) for i in range(2, 20)]  # 0.10 .. 0.95
MIN_PRECISION = 0.9
WORDINGS = {  # name: (question, inverted); a question is a yes/no unless it is a dict (a choice)
    "finished": ("Has the user finished saying what they wanted to say, so it's Aethel's turn to answer?", False),
    "complete": ("Is the user's last message a complete thought that Aethel can answer now, rather than something "
                 "cut off part-way?", False),
    "cut_off": ("Did the user stop in the middle of a sentence, so that they are going to carry on speaking?", True),
    "choice_doing": ({"type": "choice", "instructions": "What is the user doing right now?", "criteria": {
        "finished": "they have finished: what they said is a complete request, question or answer, and now it is "
                    "Aethel's turn",
        "continuing": "they paused in the middle of a thought: what they said is cut off or incomplete and they "
                      "will carry on"}}, False),
    "choice_ending": ({"type": "choice", "instructions": EOT_INSTRUCTIONS, "criteria": EOT_OPTIONS}, False),  # what ships
}


def pick(probs: list[float], labels: list[bool], min_precision: float = MIN_PRECISION) -> float | None:
    """The lowest threshold at which at least `min_precision` of what fires is a real "finished"."""
    for t in THRESHOLDS:
        m = at(probs, labels, t)
        if m["precision"] is not None and m["precision"] >= min_precision:
            return t
    return None


def split(xs: list) -> tuple[list, list]:
    return xs[0::2], xs[1::2]


def score(dev_probs: list[float], dev_labels: list[bool]) -> tuple[float, float | None]:
    """How good a wording is on dev: the recall it keeps at the precision bar (0 if it never reaches it)."""
    t = pick(dev_probs, dev_labels)
    return (at(dev_probs, dev_labels, t)["recall"] if t is not None else 0.0), t


def ask(model, rows: list[dict], question: str, invert: bool) -> tuple[list[float], float]:
    t0 = time.perf_counter()
    probs = []
    for r in rows:
        spec = question if isinstance(question, dict) else {"type": "noul", "instructions": question}
        a = model.system_one(eot_state(r["text"], r.get("previous_reply")), {"q": spec})["answers"]["q"]
        p = a["probabilities"]["finished"] if isinstance(question, dict) else a["noul"]
        probs.append(1.0 - p if invert else p)
    return probs, (time.perf_counter() - t0) * 1000 / len(rows)


def main() -> None:
    from aethel.system1.laya import LayaModel
    from aethel.system1.service import default_model_dir
    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]
    labels = [r["label"] == "finished" for r in rows]
    model = LayaModel.load(default_model_dir())
    dev_rows, test_rows = split(rows)
    dev_labels, test_labels = split(labels)
    tried = {}
    for name, (question, invert) in WORDINGS.items():
        dev_probs, ms = ask(model, dev_rows, question, invert)
        recall, t = score(dev_probs, dev_labels)
        tried[name] = {"dev_recall_at_bar": recall, "threshold": t, "ms_per_call": ms, "probs": dev_probs}
        print(f"wording {name!r}: dev threshold={t} recall at precision>={MIN_PRECISION}: {recall:.2f}  ({ms:.0f} ms/call)")
    best = max(tried, key=lambda n: (tried[n]["dev_recall_at_bar"], n == "finished"))  # a tie keeps the original
    question, invert = WORDINGS[best]
    t = tried[best]["threshold"]
    test_probs, ms = ask(model, test_rows, question, invert)
    result = {
        "n": len(rows), "model": "laya (receptron/laya-onnx@68f27df)", "wording": best, "question": question if isinstance(question, str) else json.dumps(question),
        "inverted": invert, "chosen_threshold": t, "min_dev_precision": MIN_PRECISION, "ms_per_call": ms,
        "dev": [at(tried[best]["probs"], dev_labels, x) for x in THRESHOLDS],
        "test_at_chosen": at(test_probs, test_labels, t) if t is not None else None,
        "test_ece": ece(test_probs, test_labels), "wordings_tried": {n: {k: v for k, v in d.items() if k != "probs"}
                                                                     for n, d in tried.items()},
    }
    out = aethel_home() / "eval" / "s1_eot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"chosen on dev: wording {best!r}, threshold {t}")
    print(f"held out (n={len(test_rows)}): {result['test_at_chosen']}  ECE={result['test_ece']:.3f}  {ms:.0f} ms/call")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
