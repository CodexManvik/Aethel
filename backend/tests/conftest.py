import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


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
