import os
import subprocess
import sys
from pathlib import Path

import pytest

from aethel.providers import job_object

BACKEND_DIR = Path(__file__).resolve().parents[1]
windows_only = pytest.mark.skipif(os.name != "nt", reason="Job Objects are Windows-only")

# Spawns a sleeping grandchild, puts it in the job, then dies abruptly: no
# finally blocks, no atexit, exactly like TerminateProcess from the Tauri shell.
HELPER = """
import os, subprocess, sys
from aethel.providers import job_object
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(child.pid, job_object.assign(child), flush=True)
os._exit(0)
"""


@windows_only
def test_job_kills_assigned_grandchild_when_owner_dies_abruptly():
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    synchronize, query_limited, terminate = 0x00100000, 0x1000, 0x0001

    out = subprocess.run([sys.executable, "-c", HELPER], cwd=BACKEND_DIR, capture_output=True, text=True,
                         timeout=30)
    pid_text, assigned = out.stdout.split()
    assert assigned == "True", out.stderr
    handle = kernel32.OpenProcess(synchronize | query_limited | terminate, False, int(pid_text))
    if not handle:
        return  # already gone and reaped: exactly what we want
    try:
        exited = kernel32.WaitForSingleObject(handle, 5000) == 0  # WAIT_OBJECT_0
        if not exited:
            kernel32.TerminateProcess(handle, 1)  # don't leak a 60s sleeper
        assert exited, "grandchild outlived the process that owned the job"
    finally:
        kernel32.CloseHandle(handle)


@windows_only
def test_job_has_kill_on_close_flag_and_assigns_a_real_child():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert job_object.assign(child) is True
        assert job_object.kill_on_close_enabled() is True
    finally:
        child.kill()
        child.wait()


def test_assign_is_a_harmless_noop_off_windows(monkeypatch):
    monkeypatch.setattr(job_object, "SUPPORTED", False)
    assert job_object.assign(12345) is False
