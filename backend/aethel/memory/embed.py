"""bge-small-en-v1.5 (the model v1 used) through ONNX Runtime on the CPU:
importing sentence-transformers alone took ~30 s here, this loads in ~1 s,
and it never competes with a local LLM for the GPU."""
import threading
import time

import numpy as np

REPO = "BAAI/bge-small-en-v1.5"
REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
MAX_TOKENS = 512
RETRY_AFTER_S = 60  # after a failed load (offline, no cached model), fail fast instead of retrying every call
_lock = threading.Lock()
_session = None
_tokenizer = None
_failed: tuple[float, str] | None = None  # (when, why) the last load failed


def _load() -> None:
    global _session, _tokenizer
    import onnxruntime as ort
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer
    tok = Tokenizer.from_file(hf_hub_download(REPO, "tokenizer.json", revision=REVISION))
    tok.enable_truncation(MAX_TOKENS)
    tok.enable_padding()
    _session = ort.InferenceSession(hf_hub_download(REPO, "onnx/model.onnx", revision=REVISION),
                                    providers=["CPUExecutionProvider"])
    _tokenizer = tok


def embed(texts: list[str]) -> np.ndarray:
    """L2-normalised vectors (CLS pooling, as bge specifies), one row per text. Blocking: call from a thread."""
    global _failed
    with _lock:
        if _session is None:
            if _failed is not None and time.monotonic() - _failed[0] < RETRY_AFTER_S:
                raise RuntimeError(f"the embedder isn't available: {_failed[1]}")
            try:
                _load()
            except Exception as exc:
                _failed = (time.monotonic(), repr(exc)[:200])
                raise
            _failed = None
    enc = _tokenizer.encode_batch(texts)
    ids = np.array([e.ids for e in enc], dtype=np.int64)
    mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
    feeds = {"input_ids": ids, "attention_mask": mask}
    if any(i.name == "token_type_ids" for i in _session.get_inputs()):
        feeds["token_type_ids"] = np.zeros_like(ids)
    cls = _session.run(None, feeds)[0][:, 0]
    return (cls / np.linalg.norm(cls, axis=1, keepdims=True)).astype(np.float32)
