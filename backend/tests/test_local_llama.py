import logging
import socket
import subprocess
from pathlib import Path

import pytest

from aethel.providers import local_llama
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


class FakePopen:
    def __init__(self, *args, hang=False, **kwargs):
        self.args = args
        self.hang = hang
        self.returncode = None
        self.pid = 4242
        self.terminated = self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        if self.hang:
            raise subprocess.TimeoutExpired("llama-server", timeout)
        self.returncode = 0
        return 0

    def kill(self):
        self.killed = True


def test_stop_terminates_process_and_clears_it(tmp_path):
    llama = _llama(tmp_path)
    proc = FakePopen()
    llama._process = proc
    llama.stop()
    assert proc.terminated is True and proc.killed is False
    assert llama._process is None
    llama.stop()  # idempotent with nothing running


def test_stop_kills_when_terminate_times_out(tmp_path):
    llama = _llama(tmp_path)
    proc = FakePopen(hang=True)
    llama._process = proc
    llama.stop()
    assert proc.terminated is True and proc.killed is True
    assert llama._process is None


def _spawnable(tmp_path, monkeypatch, assign_result):
    """A LocalLlama whose spawn is faked: model + binary exist, a free port,
    is_up() is False until the fake process has been started."""
    model = _write(tmp_path / "models" / "llm" / "m.gguf", 10)
    binary = _write(tmp_path / "bin" / "llama-server.exe", 1)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    llama = LocalLlama(lambda: LocalLLMSettings(), base_url=f"http://127.0.0.1:{free_port}/v1",
                       models_dir=tmp_path / "models")
    spawned, assigned = [], []
    monkeypatch.setattr(llama, "find_binary", lambda: binary)
    monkeypatch.setattr(llama, "is_up", lambda: bool(spawned))
    monkeypatch.setattr(local_llama.subprocess, "Popen", lambda *a, **k: spawned.append(FakePopen(*a)) or spawned[-1])
    monkeypatch.setattr(local_llama.job_object, "assign", lambda p: assigned.append(p) or assign_result)
    monkeypatch.setattr(local_llama.job_object, "SUPPORTED", True)
    return llama, model, spawned, assigned


def test_ensure_running_assigns_spawned_server_to_job(tmp_path, monkeypatch):
    llama, _, spawned, assigned = _spawnable(tmp_path, monkeypatch, assign_result=True)
    llama.ensure_running()
    assert len(spawned) == 1 and assigned == spawned
    assert llama._process is spawned[0]


def test_failed_job_assign_warns_but_still_starts(tmp_path, monkeypatch, caplog):
    llama, _, spawned, assigned = _spawnable(tmp_path, monkeypatch, assign_result=False)
    with caplog.at_level(logging.WARNING, logger="aethel.local_llama"):
        llama.ensure_running()
    assert assigned == spawned and llama._process is spawned[0]
    assert any("job" in r.getMessage() for r in caplog.records)
