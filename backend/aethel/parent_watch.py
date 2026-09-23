"""Exit the backend when the process that launched it (the Tauri shell) dies.

Without this, a crashed shell leaves an orphaned backend holding the port.
"""
import os
import threading


def watch_parent(pid: int) -> None:
    if os.name == "nt":
        import ctypes

        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return

        def wait() -> None:
            ctypes.windll.kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
            os._exit(0)
    else:
        import time

        def wait() -> None:
            while True:
                try:
                    os.kill(pid, 0)
                except OSError:
                    os._exit(0)
                time.sleep(2)

    threading.Thread(target=wait, name="parent-watch", daemon=True).start()
