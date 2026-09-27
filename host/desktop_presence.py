"""A held advisory lock keeps the shared bridge alive for a desktop window."""
import fcntl

from desktop_ipc import runtime_dir


def desktop_active():
    try:
        with (runtime_dir() / "desktop.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
        return False
    except OSError:
        return False
