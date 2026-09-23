"""Tie child processes (llama-server) to the lifetime of this backend process.

Windows: one process-wide Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.
Its handle is deliberately never closed, so whenever this process ends, for any
reason (clean shutdown, parent_watch's os._exit, TerminateProcess from the
Tauri shell, a crash), the OS closes the handle and kills every process in the
job. Nothing in Python has to run for that to happen.

Elsewhere this module is a no-op: `assign` returns False.
"""
import logging
import os
import subprocess
import threading

log = logging.getLogger("aethel.job_object")

SUPPORTED = os.name == "nt"
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9  # JobObjectExtendedLimitInformation
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001

_lock = threading.Lock()
_job = None  # the job HANDLE; intentionally lives until the process dies

if SUPPORTED:
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),  # ULONG_PTR
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    # A private WinDLL instance, so these prototypes don't leak into ctypes.windll.
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    _k32.QueryInformationJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL

    def _create_job():
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _k32.SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                                            ctypes.byref(info), ctypes.sizeof(info)):
            err = ctypes.get_last_error()
            _k32.CloseHandle(job)
            raise OSError(err, "SetInformationJobObject failed")
        return job


def _get_job():
    global _job
    with _lock:
        if _job is None:
            _job = _create_job()
        return _job


def assign(pid_or_process: "int | subprocess.Popen") -> bool:
    """Put a process in the kill-on-close job. Accepts a Popen object (its own
    handle is used) or a pid. Returns False if unsupported or it failed."""
    if not SUPPORTED:
        return False
    try:
        job = _get_job()
    except OSError as exc:
        log.warning("could not create the child-process job: %s", exc)
        return False
    if isinstance(pid_or_process, subprocess.Popen):
        return bool(_k32.AssignProcessToJobObject(job, int(pid_or_process._handle)))
    handle = _k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, int(pid_or_process))
    if not handle:
        return False
    try:
        return bool(_k32.AssignProcessToJobObject(job, handle))
    finally:
        _k32.CloseHandle(handle)


def kill_on_close_enabled() -> bool:
    """True if the job exists and the OS reports KILL_ON_JOB_CLOSE set on it."""
    if not SUPPORTED:
        return False
    info = _ExtendedLimitInformation()
    ok = _k32.QueryInformationJobObject(_get_job(), JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                                        ctypes.byref(info), ctypes.sizeof(info), None)
    return bool(ok) and bool(info.BasicLimitInformation.LimitFlags & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
