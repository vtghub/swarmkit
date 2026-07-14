"""Process supervision for swarmkitd: start it as a detached background
process, check whether it's alive, and stop it — no daemon library magic,
just a pidfile and signals."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

from swarmkit.daemon.server import pid_path, socket_path

START_TIMEOUT_SECS = 5.0
STOP_TIMEOUT_SECS = 5.0
POLL_INTERVAL_SECS = 0.1


def _read_pid() -> int | None:
    path = pid_path()
    if not path.exists():
        return None
    try:
        return int(path.read_text().strip())
    except (ValueError, OSError):
        return None


def is_running() -> int | None:
    """Return the daemon's real OS pid if it's alive, else None."""
    pid = _read_pid()
    if pid is None:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid  # exists, owned by someone else — still "running"
    return pid


def start(concurrency: int = 8) -> int:
    """Start swarmkitd if it isn't already running. Returns its real pid."""
    existing = is_running()
    if existing is not None:
        return existing

    pid_path().parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["SWARMKIT_CONCURRENCY"] = str(concurrency)
    proc = subprocess.Popen(
        [sys.executable, "-m", "swarmkit.daemon.server"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    deadline = time.monotonic() + START_TIMEOUT_SECS
    while time.monotonic() < deadline:
        if socket_path().exists():
            return proc.pid
        if proc.poll() is not None:
            raise RuntimeError(f"swarmkitd exited immediately with code {proc.returncode}")
        time.sleep(POLL_INTERVAL_SECS)
    raise RuntimeError(f"swarmkitd did not create its socket within {START_TIMEOUT_SECS}s")


def stop() -> bool:
    """Stop swarmkitd if running. Returns True if it was running and stopped."""
    pid = _read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False

    deadline = time.monotonic() + STOP_TIMEOUT_SECS
    while time.monotonic() < deadline:
        if is_running() is None:
            return True
        time.sleep(POLL_INTERVAL_SECS)
    return False
