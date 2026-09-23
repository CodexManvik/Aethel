import os
import json
import re
import threading
import numpy as np

from config import (
    TV_INDEX_PATH, METADATA_PATH, RETRIEVAL_INITIAL_K,
    RETRIEVAL_MAX_DOCS, RETRIEVAL_FINAL_RETURN_COUNT,
    ENABLE_CONTENT_GUARDRAILS, NORMALIZE_SLANG,
)
import embeddings

# --- SYSTEM CONFIGURATION ---
# Heavy assets (embedding model, turbovec index, reranker) are loaded lazily on
# the first retrieval so the API server boots instantly.

SLANG_MAP = {
    r'\b2\b': 'to',
    r'\b4\b': 'for',
    r'\bu\b': 'you',
    r'\br\b': 'are',
    r'\bur\b': 'your',
    r'\bn\b': 'and'
}

RETRIEVAL_PARAMS = {
    "initial_k": RETRIEVAL_INITIAL_K,
    "max_docs": RETRIEVAL_MAX_DOCS,
    "final_return_count": RETRIEVAL_FINAL_RETURN_COUNT
}

# --- LAZY ENGINE STATE ---
_lock = threading.Lock()
_index = None
_metadata_lookup: dict = {}
_index_loaded = False


def _ensure_index():
    """Loads the turbovec index + metadata once, on first use."""
    global _index, _metadata_lookup, _index_loaded
    if _index_loaded:
        return
    with _lock:
        if _index_loaded:
            return
        if os.path.exists(TV_INDEX_PATH) and os.path.exists(METADATA_PATH):
            try:
                from turbovec import IdMapIndex
                _index = IdMapIndex.load(TV_INDEX_PATH)
                with open(METADATA_PATH, "r", encoding="utf-8") as f:
                    _metadata_lookup = json.load(f)
                print("[RETRIEVAL ENGINE] Loaded Turbovec index and lookup maps.")
            except Exception as e:
                # An incompatible/corrupt index must not take down chat — degrade
                # to "no episodic memory" and tell the user how to fix it.
                _index = None
                _metadata_lookup = {}
                print(
                    f"[RETRIEVAL ENGINE] Could not load episodic memory index ({e}). "
                    "Episodic recall is DISABLED. Rebuild it with:  "
                    "py -3.11 backend/build_memory_db.py"
                )
        else:
            print("[RETRIEVAL ENGINE] No episodic memory index found (run build_memory_db.py to create one).")
        _index_loaded = True


# --- CORE LOGIC ---
def clean_text_shorthand(text: str) -> str:
    """Standardizes chat slang using the dynamic lookup map configuration."""
    if not NORMALIZE_SLANG:
        return text

    for pattern, replacement in SLANG_MAP.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def enforce_content_guardrails(docs: list) -> list:
    """Enforces document count limits based on configuration."""
    if not ENABLE_CONTENT_GUARDRAILS:
        return docs
    return docs[:RETRIEVAL_PARAMS["max_docs"]]


def retrieve_memories(query: str):
    _ensure_index()
    if _index is None or not _metadata_lookup:
        return []

    # 1. Vector query structure generation (shared embedding model)
    query_vector = embeddings.encode(query, normalize=False).reshape(1, -1)

    # 2. Hardware accelerated 4-bit retrieval pass
    _, retrieved_ids = _index.search(query_vector, k=RETRIEVAL_PARAMS["initial_k"])

    docs = [
        _metadata_lookup[str(uid)]["raw_text"]
        for uid in retrieved_ids[0]
        if str(uid) in _metadata_lookup
    ]

    if not docs:
        return []

    # 3. Execution of Rerank, Sanitization, and Guardrail pipelines
    from reranker import rerank
    reranked_docs = rerank(query, docs)
    cleaned_docs = [clean_text_shorthand(doc) for doc in reranked_docs]
    safeguarded_docs = enforce_content_guardrails(cleaned_docs)

    # 4. Final context return slice boundary slice
    return safeguarded_docs[:RETRIEVAL_PARAMS["final_return_count"]]
