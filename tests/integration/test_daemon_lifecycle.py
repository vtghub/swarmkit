"""Proves the daemon subprocess actually starts, serves the worker pool over a
real Unix domain socket, and can be stopped — the CLI's `daemon start|stop`
wiring end to end, not a mock."""

from __future__ import annotations

import asyncio
import shutil
import tempfile

import pytest

from swarmkit.cli import daemon_client
from swarmkit.daemon import supervisor


@pytest.fixture
def isolated_runtime_dir(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="swarmkit-test-")
    monkeypatch.setenv("SWARMKIT_RUNTIME_DIR", tmp)
    yield tmp
    supervisor.stop()
    shutil.rmtree(tmp, ignore_errors=True)


async def test_daemon_start_submit_status_stop(isolated_runtime_dir, tmp_path):
    assert supervisor.is_running() is None

    pid = supervisor.start(concurrency=2)
    assert pid > 0
    assert supervisor.is_running() == pid

    ping = await daemon_client.ping()
    assert ping["ok"] is True
    assert ping["pid"] == pid

    task_id = await daemon_client.submit_task(
        ["echo", "daemon-proof"],
        jail_root=str(tmp_path),
        workdir=str(tmp_path),
        allowed_executables=["echo"],
    )

    status = None
    for _ in range(100):
        status = await daemon_client.task_status(task_id)
        if status["status"] == "completed":
            break
        await asyncio.sleep(0.05)
    assert status is not None
    assert status["status"] == "completed"
    assert status["result"]["pid"] > 0
    assert status["result"]["pid"] != pid, "task PID must be its own subprocess, not the daemon's"
    assert "daemon-proof" in status["result"]["stdout"]

    tasks = await daemon_client.list_tasks()
    assert any(tid == task_id for tid, _ in tasks)

    assert supervisor.stop() is True
    assert supervisor.is_running() is None


async def test_second_start_is_a_no_op_and_returns_existing_pid(isolated_runtime_dir):
    first_pid = supervisor.start(concurrency=1)
    second_pid = supervisor.start(concurrency=1)
    assert first_pid == second_pid
