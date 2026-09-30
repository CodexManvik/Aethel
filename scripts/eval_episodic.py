"""Measure the episodic recall floor (Phase 3 spec §3, §8) on backend/aethel/eval/episodic.json.

The scripted conversations are indexed with the real bge embedder in a throwaway database. Each query
recalls its top K exchanges above the floor, as chat does: that's correct if one of them was expected, wrong
if none was, and unwanted if the query has nothing relevant in the history. The floor is chosen on the even
queries to maximise correct recalls minus wrong and unwanted ones, then reported on the odd queries.
(The first version scored only the top hit; that stricter number is still reported.)
Writes ~/.aethel/eval/episodic.json. Usage: py -3.11 scripts/eval_episodic.py"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

DATA = ROOT / "backend" / "aethel" / "eval" / "episodic.json"
THRESHOLDS = [round(0.5 + 0.01 * i, 2) for i in range(41)]  # 0.50 … 0.90
K = 3  # MemorySettings.episodes_k: chat recalls up to this many earlier exchanges


def at(rows: list[dict], t: float) -> dict:
    """As chat uses it: each query recalls its top hits (up to K) that reach the floor t.
    rows: {"hits": [[ref, score], …] best first, "expect": [ref, …]}. A query is correct when something expected
    is recalled, wrong when only unexpected exchanges are, unwanted when it has no answer but recalls anything."""
    correct = wrong = unwanted = 0
    for r in rows:
        recalled = [ref for ref, score in r["hits"] if score >= t]
        if not recalled:
            continue
        if not r["expect"]:
            unwanted += 1
        elif any(ref in r["expect"] for ref in recalled):
            correct += 1
        else:
            wrong += 1
    answerable = sum(1 for r in rows if r["expect"])
    fired = correct + wrong + unwanted
    return {"threshold": t, "correct": correct, "wrong": wrong, "unwanted": unwanted,
            "recall": correct / answerable if answerable else None,
            "precision": correct / fired if fired else None}


def choose_floor(rows: list[dict]) -> float:
    """Most correct recalls net of wrong and unwanted ones; ties go to the higher (safer) floor."""
    return max(THRESHOLDS, key=lambda t: (at(rows, t)["correct"] - at(rows, t)["wrong"] - at(rows, t)["unwanted"], t))


def main() -> None:
    from aethel.memory.embed import embed
    from aethel.memory.episodic import EpisodicIndex
    from aethel.paths import aethel_home
    from aethel.store.db import Database
    from aethel.store.repos import ConversationRepo, MessageRepo

    data = json.loads(DATA.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "eval.db")
        convs, msgs = ConversationRepo(db), MessageRepo(db)
        index = EpisodicIndex(db, embed, Path(tmp) / "index.tvim")
        ref = {}  # assistant message id -> "conversation:index"
        for c in data["conversations"]:
            conv = convs.create()
            for i, (user, reply) in enumerate(c["exchanges"]):
                u, a = msgs.add(conv.id, "user", user), msgs.add(conv.id, "assistant", reply)
                ref[a.id] = f"{c['id']}:{i}"
        index.catch_up()
        rows = []
        for q in data["queries"]:
            hits = index.search(q["query"], k=K, min_score=-1.0)
            rows.append({"query": q["query"], "expect": q["expect"],
                         "hits": [[ref[e.assistant_message_id], round(s, 4)] for e, s in hits]})
        db.close()
    dev, test = rows[0::2], rows[1::2]
    floor = choose_floor(dev)
    answerable = [r for r in rows if r["expect"]]
    result = {"n_queries": len(rows), "embedder": "bge-small-en-v1.5 (ONNX)", "k": K, "floor": floor,
              "dev_at_floor": at(dev, floor), "test_at_floor": at(test, floor),
              # the stricter first metric (top hit only, no floor), reported for honesty
              "top1_right_when_answerable": sum(r["hits"][0][0] in r["expect"] for r in answerable) / len(answerable),
              "topk_right_when_answerable": sum(any(h[0] in r["expect"] for h in r["hits"]) for r in answerable)
              / len(answerable),
              "rows": rows}
    out = aethel_home() / "eval" / "episodic.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    for r in rows:
        mark = "-- " if not r["expect"] else ("ok " if any(h[0] in r["expect"] for h in r["hits"]) else "BAD")
        print(f"{r['hits'][0][1]:.3f} {mark} {r['query']}  →  {', '.join(f'{h[0]} {h[1]:.2f}' for h in r['hits'])}")
    print(f"answerable: top-1 right {result['top1_right_when_answerable']:.2f}, "
          f"any of top-{K} right {result['topk_right_when_answerable']:.2f} (no floor)")
    print(f"floor chosen on dev: {floor}; dev {result['dev_at_floor']}")
    print(f"held out: {result['test_at_floor']}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
