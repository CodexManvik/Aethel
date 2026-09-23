from pathlib import Path

import pytest

from aethel.providers.local_llama import LocalLLMUnavailable, LocalLlama
from aethel.settings import LocalLLMSettings


def _write(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def _llama(tmp_path, **settings) -> LocalLlama:
    return LocalLlama(lambda: LocalLLMSettings(**settings), models_dir=tmp_path / "models")


def test_discover_prefers_largest_non_mmproj(tmp_path):
    llm_dir = tmp_path / "models" / "llm"
    _write(llm_dir / "small.gguf", 10)
    big = _write(llm_dir / "big.gguf", 50)
    proj = _write(llm_dir / "mmproj-f16.gguf", 100)
    assert _llama(tmp_path).discover_model() == (big, proj)


def test_discover_honours_settings_override(tmp_path):
    _write(tmp_path / "models" / "llm" / "big.gguf", 50)
    chosen = _write(tmp_path / "elsewhere" / "mine.gguf", 5)
    assert _llama(tmp_path, model_path=str(chosen)).discover_model() == (chosen, None)


def test_discover_returns_none_when_empty(tmp_path):
    assert _llama(tmp_path).discover_model() == (None, None)


def test_find_binary_honours_env_override(tmp_path, monkeypatch):
    exe = _write(tmp_path / "bin" / "llama-server.exe", 1)
    monkeypatch.setenv("LLAMA_SERVER_BINARY", str(exe.parent))
    monkeypatch.setattr("shutil.which", lambda name: None)
    found = _llama(tmp_path).find_binary()
    assert found is not None and found.parent == exe.parent


def test_build_command_flags(tmp_path):
    llama = _llama(tmp_path, context_size=4096, threads=6, gpu_layers=20)
    cmd = llama.build_command(Path("llama-server"), Path("m.gguf"), Path("p.gguf"), 8080)
    joined = " ".join(cmd)
    for fragment in ("-m m.gguf", "--port 8080", "-c 4096", "-t 6", "-ngl 20", "--mmproj p.gguf",
                     "--context-shift", "--cache-reuse 256"):
        assert fragment in joined


def test_build_command_omits_ngl_for_cpu(tmp_path):
    cmd = _llama(tmp_path, gpu_layers=0).build_command(Path("s"), Path("m.gguf"), None, 8080)
    assert "-ngl" not in cmd and "--mmproj" not in cmd


def test_ensure_running_explains_missing_model(tmp_path, monkeypatch):
    llama = _llama(tmp_path)
    monkeypatch.setattr(llama, "is_up", lambda: False)
    with pytest.raises(LocalLLMUnavailable, match="models"):
        llama.ensure_running()


def test_ensure_running_is_noop_when_server_up(tmp_path, monkeypatch):
    llama = _llama(tmp_path)
    monkeypatch.setattr(llama, "is_up", lambda: True)
    llama.ensure_running()  # no model on disk, but nothing to spawn
