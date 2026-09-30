import hashlib
import re
import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def fake_embed(texts):
    """Bag of words hashed into 256 dims: shared words mean similar vectors."""
    out = np.zeros((len(texts), 256), dtype=np.float32)
    for i, t in enumerate(texts):
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)


@pytest.fixture(autouse=True)
def no_real_embedder(monkeypatch):
    """Tests never download or load bge: anything that embeds gets the bag-of-words fake."""
    monkeypatch.setattr("aethel.memory.embed.embed", fake_embed)


@pytest.fixture(autouse=True)
def aethel_home(tmp_path, monkeypatch):
    """Every test gets an isolated ~/.aethel and a clean auth/key environment."""
    home = tmp_path / "aethel_home"
    monkeypatch.setenv("AETHEL_HOME", str(home))
    monkeypatch.setenv("AETHEL_DEV", "1")
    monkeypatch.setenv("AETHEL_MCP", "0")  # no real desktop/Office servers in tests
    monkeypatch.setenv("AETHEL_SYSTEM1", "0")  # and no 1.7 GB model download
    monkeypatch.delenv("AETHEL_TOKEN", raising=False)
    for var in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "CUSTOM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture
def anyio_backend():
    return "asyncio"
