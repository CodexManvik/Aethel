"""Laya (convaiinnovations/laya): a Jev-compatible System 1 decision model.

A Python port of receptron/laya's ONNX inference (src/sequence.ts + src/laya.ts),
which itself ports the checkpoint's rl_common.py / rl_agent_api.py. One forward
pass answers every question about one state; nothing is generated."""
import json
import math
from pathlib import Path

import numpy as np

QTYPES = {"choice": 0, "score": 1, "noul": 2}
_QTYPE_NAMES = ["choice", "score", "noul"]
MASK_TOKEN = "[MASK]"
MAX_OPTION_TOKENS = 48


def to_internal(q: dict) -> tuple[str, str, object]:
    """(type, instructions, criteria), with a choice list turned into {option: None}."""
    kind = q["type"]
    crit = q.get("criteria")
    if kind == "choice" and isinstance(crit, list):
        crit = {c: None for c in crit}
    ins = q["instructions"] if isinstance(q["instructions"], str) else json.dumps(q["instructions"])
    return kind, ins, crit


def render_options(kind: str, crit) -> list[str]:
    """Option texts in label order. A noul is always [false, true], so p[1] is the noul."""
    if kind == "choice":
        return [f"{k}: {v}" if v else k for k, v in crit.items()]
    if kind == "score":
        return [f"level {i}: {c}" for i, c in enumerate(crit)]
    crit = crit or {}
    return ["false: " + (crit.get("false") or "no, the statement does not hold"),
            "true: " + (crit.get("true") or "yes, the statement holds")]


def serialize_state(state) -> str:
    return state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)


def temp_bucket(qtype: int, k: int) -> str:
    size = "2" if k <= 2 else "3-5" if k <= 5 else "6-10" if k <= 10 else "11+"
    return f"{_QTYPE_NAMES[qtype]}:{size}"


def confidence(p: list[float]) -> float:
    """Jev-style: 1 - normalised entropy of the answer distribution."""
    if len(p) < 2:
        return 1.0
    return 1 - (-sum(x * math.log(max(x, 1e-12)) for x in p)) / math.log(len(p))


def softmax(z: list[float]) -> list[float]:
    m = max(z)
    e = [math.exp(v - m) for v in z]
    s = sum(e)
    return [v / s for v in e]


def build_sequence(encode, special: dict, state, kind: str, ins: str, crit, max_len: int,
                   head_max_len: int) -> tuple[list[int], list[int]]:
    """[CLS] <type> question: instructions [SEP] [MASK] opt0 [MASK] opt1 ... [SEP] state [SEP],
    plus the position of each option's [MASK] marker."""
    def scrub(s: str) -> str:
        return " ".join(s.split(MASK_TOKEN)) if MASK_TOKEN in s else s  # user text can't inject a marker

    head = encode(f"{kind} question: {scrub(ins)}")
    opts = [[special["mask"], *encode(" " + scrub(o))[:MAX_OPTION_TOKENS]] for o in render_options(kind, crit)]
    budget = head_max_len - sum(len(o) for o in opts)
    if budget < 16:  # too many or too long options: shrink every option evenly
        per = max(4, (head_max_len - 16) // max(1, len(opts)))
        opts = [o[:per] for o in opts]
        budget = head_max_len - sum(len(o) for o in opts)
    head = head[:max(8, budget)]
    seq = [special["cls"], *head, special["sep"]]
    markers = []
    for o in opts:
        markers.append(len(seq))
        seq.extend(o)
    seq.append(special["sep"])
    room = max(0, max_len - len(seq) - 1)
    seq.extend(encode(scrub(serialize_state(state)))[:room])
    seq.append(special["sep"])
    return seq[:max_len], [m for m in markers if m < max_len]


class LayaModel:
    def __init__(self, session, tokenizer, config: dict, special: dict):
        self.session = session
        self.tokenizer = tokenizer
        self.config = config
        self.special = special

    @classmethod
    def load(cls, model_dir: Path, threads: int | None = None) -> "LayaModel":
        import onnxruntime as ort
        from tokenizers import Tokenizer

        config = json.loads((model_dir / "laya_config.json").read_text(encoding="utf-8"))
        tok = Tokenizer.from_file(str(model_dir / "tokenizer" / "tokenizer.json"))
        special = {name.strip("[]").lower(): tok.token_to_id(name) for name in ("[CLS]", "[SEP]", "[MASK]", "[PAD]")}
        if any(v is None for v in special.values()):
            raise ValueError("the tokenizer is missing a special token")
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if threads:
            options.intra_op_num_threads = threads
        session = ort.InferenceSession(str(model_dir / "laya.onnx"), options, providers=["CPUExecutionProvider"])
        return cls(session, tok, config, special)

    def _encode(self, text: str) -> list[int]:
        return self.tokenizer.encode(text, add_special_tokens=False).ids

    def system_one(self, state, questions: dict) -> dict:
        """Answer every question about `state` in one forward pass (Jev's response shape)."""
        if not questions:
            raise ValueError("at least one question is required")
        items = []
        for qid, q in questions.items():
            kind, ins, crit = to_internal(q)
            ids, markers = build_sequence(self._encode, self.special, state, kind, ins, crit,
                                          self.config["max_len"], self.config["head_max_len"])
            if len(markers) != len(render_options(kind, crit)):
                raise ValueError(f"question {qid!r}: options don't fit in {self.config['head_max_len']} tokens")
            items.append((qid, kind, crit, ids, markers))

        n, width = len(items), max(len(i[3]) for i in items)
        k_max = max(len(i[4]) for i in items)
        input_ids = np.full((n, width), self.special["pad"], dtype=np.int64)
        attention = np.zeros((n, width), dtype=np.int64)
        marker_pos = np.zeros((n, k_max), dtype=np.int64)
        marker_mask = np.zeros((n, k_max), dtype=bool)
        qtype = np.zeros(n, dtype=np.int64)
        for r, (_, kind, _, ids, markers) in enumerate(items):
            input_ids[r, :len(ids)] = ids
            attention[r, :len(ids)] = 1
            marker_pos[r, :len(markers)] = markers
            marker_mask[r, :len(markers)] = True
            qtype[r] = QTYPES[kind]
        logits, act = self.session.run(["logits", "act_probs"], {
            "input_ids": input_ids, "attention_mask": attention, "marker_pos": marker_pos,
            "marker_mask": marker_mask, "qtype": qtype})

        answers = {}
        for r, (qid, kind, crit, _, markers) in enumerate(items):
            k = len(markers)
            t = self.config["temperature_by_options"].get(temp_bucket(QTYPES[kind], k)) \
                or self.config["temperature"][QTYPES[kind]] or 1.0
            p = softmax([float(v) / t for v in logits[r, :k]])
            ext = {"act_probability": float(act[r, 0])}
            if kind == "choice":
                keys = list(crit)
                best = max(range(k), key=p.__getitem__)
                answers[qid] = {"type": "choice", "choice": keys[best],
                                "probabilities": {kk: round(p[i], 4) for i, kk in enumerate(keys)},
                                "confidence": round(confidence(p), 4), "rl_agent": ext}
            elif kind == "score":
                answers[qid] = {"type": "score", "score": round(sum(i * v for i, v in enumerate(p)), 4),
                                "legend": {str(i): c for i, c in enumerate(crit)},
                                "probabilities": {str(i): round(v, 4) for i, v in enumerate(p)},
                                "confidence": round(confidence(p), 4), "rl_agent": ext}
            else:
                answers[qid] = {"type": "noul", "noul": round(p[1], 4), "rl_agent": ext}
        return {"model": "laya", "answers": answers,
                "usage": {"input_tokens": sum(len(i[3]) for i in items), "output_tokens": 0}}
