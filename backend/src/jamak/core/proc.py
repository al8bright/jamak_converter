"""Child-process helpers.

On Windows every child is placed in a Job Object with KILL_ON_JOB_CLOSE, so if this Python
process dies (crash, Tauri kills the sidecar) the children (llama-server) die with it and
never keep holding GPU memory or a port.

macOS has no Job Objects: children are remembered and killed at normal exit (atexit, which uvicorn
reaches on SIGTERM) and by the parent-pid watcher before it hard-exits. A SIGKILL of this process
still leaves them running; llama-server then exits by itself only when its port is reused.
"""

from __future__ import annotations

import atexit
import socket
import subprocess
import sys
import threading
import weakref

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_job_handle = None


def _windows_job():
    global _job_handle
    if _job_handle is not None:
        return _job_handle
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class EXTENDED(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    if not ok:
        return None
    _job_handle = (kernel32, job)
    return _job_handle


_children: weakref.WeakSet[subprocess.Popen] = weakref.WeakSet()
_children_lock = threading.Lock()


def kill_children() -> None:
    """Kill every child bound with bind_to_lifetime that is still running (POSIX)."""
    with _children_lock:
        procs = list(_children)
    for p in procs:
        try:
            if p.poll() is None:
                p.kill()
        except OSError:
            pass


atexit.register(kill_children)


def bind_to_lifetime(proc: subprocess.Popen) -> None:
    """Kill `proc` automatically when this process exits (Job Object on Windows, exit hooks elsewhere)."""
    if sys.platform != "win32":
        with _children_lock:
            _children.add(proc)
        return
    job = _windows_job()
    if not job:
        return
    kernel32, handle = job
    from ctypes import wintypes

    kernel32.OpenProcess.restype = wintypes.HANDLE
    ph = kernel32.OpenProcess(0x0100 | 0x0001, False, proc.pid)  # SET_QUOTA | TERMINATE
    if ph:
        kernel32.AssignProcessToJobObject(handle, ph)
        kernel32.CloseHandle(ph)


def exit_when_parent_dies(pid: int) -> None:
    """Background watcher: hard-exit this process when `pid` exits (sidecar safety net)."""
    import os
    import threading

    def watch() -> None:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.OpenProcess.restype = wintypes.HANDLE
            h = k.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if not h:
                os._exit(0)  # parent already gone
            k.WaitForSingleObject(h, 0xFFFFFFFF)
        else:
            import time

            while True:
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(1.0)
            kill_children()  # os._exit skips atexit
        os._exit(0)

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
