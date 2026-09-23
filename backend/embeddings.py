"""
embeddings.py

Single shared SentenceTransformer instance for the whole backend.

Before this module existed, memory.py, rule_store.py, and knowledge_store.py
each constructed their own copy of the same embedding model — three copies of
identical weights in RAM/VRAM and three model loads at import time. Everything
now funnels through this lazy singleton: the first component that needs
embeddings pays the load cost once, everyone else reuses it.

persona_distillation.py intentionally keeps its own (different, 384-dim MiniLM)
model because its stored style vectors depend on that exact model.
"""

from __future__ import annotations

import threading
from typing import Optional

import numpy as np

_lock = threading.Lock()
_model = None
_dim: Optional[int] = None


def get_model():
    """Returns the shared SentenceTransformer, loading it on first use."""
    global _model, _dim
    if _model is not None:
        return _model
    with _lock:
        if _model is not None:
            return _model
        import torch
        from sentence_transformers import SentenceTransformer
        from config import EMBEDDING_MODEL, MAX_VRAM_ALLOCATION

        # For 4GB VRAM budgets, keep secondary pipelines on CPU so CUDA
        # allocations can't OOM-crash the primary LLM server.
        device = "cuda" if (torch.cuda.is_available() and MAX_VRAM_ALLOCATION > 4) else "cpu"
        print(f"[EMBEDDINGS] Loading shared embedding model on {device.upper()}...")
        _model = SentenceTransformer(EMBEDDING_MODEL, device=device)
        # Method was renamed across sentence-transformers versions; try the new
        # name first, fall back to the old one.
        try:
            _dim = _model.get_embedding_dimension()
        except AttributeError:
            _dim = _model.get_sentence_embedding_dimension()
        return _model


def get_dim() -> int:
    get_model()
    assert _dim is not None
    return _dim


def encode(text, normalize: bool = True) -> np.ndarray:
    """Encodes a string (or list of strings) to float32 numpy embeddings."""
    vecs = get_model().encode(text, normalize_embeddings=normalize)
    return np.asarray(vecs, dtype=np.float32)
