"""
reranker.py

Cross-encoder reranking for episodic memory retrieval.
The model is loaded lazily on first use so importing this module (and the
server boot path that reaches it) stays instant.
"""

import threading

_lock = threading.Lock()
_tokenizer = None
_model = None
_device = None


def _ensure_model():
    global _tokenizer, _model, _device
    if _model is not None:
        return
    with _lock:
        if _model is not None:
            return
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        from config import RERANKER_MODEL, MAX_VRAM_ALLOCATION

        # For 4GB VRAM budgets, keep secondary pipelines on CPU so CUDA
        # allocations can't OOM-crash the primary LLM server.
        _device = "cuda" if (torch.cuda.is_available() and MAX_VRAM_ALLOCATION > 4) else "cpu"
        print(f"[AFFECTIVE RERANK] Loading reranker model on {_device.upper()}...")
        _tokenizer = AutoTokenizer.from_pretrained(RERANKER_MODEL)
        _model = AutoModelForSequenceClassification.from_pretrained(RERANKER_MODEL).to(_device)
        _model.eval()


def rerank(query, documents):
    if not documents:
        return []

    _ensure_model()
    import torch

    pairs = [[query, doc] for doc in documents]

    inputs = _tokenizer(
        pairs,
        padding=True,
        truncation=True,
        return_tensors="pt"
    )
    inputs = {k: v.to(_device) for k, v in inputs.items()}

    with torch.no_grad():
        scores = _model(**inputs).logits.squeeze(-1)

    scored = list(zip(documents, scores.tolist()))
    scored.sort(key=lambda x: x[1], reverse=True)

    return [x[0] for x in scored[:5]]
