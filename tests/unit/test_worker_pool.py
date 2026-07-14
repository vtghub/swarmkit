"""Concurrency proof: N tasks dispatched through the Rust worker pool complete
in ~max(latency), not sum(latency) — real parallel execution, not just
registered state.
"""

from __future__ import annotations

import asyncio
import time

from swarmkit import _native

N = 5
SLEEP_SECS = 0.4


async def _wait_for(pool, task_id: str) -> dict:
    while True:
        status = await pool.status(task_id)
        if status["status"] in ("completed", "failed"):
            return status
        await asyncio.sleep(0.02)


async def test_worker_pool_runs_tasks_concurrently(tmp_path):
    pool = _native.WorkerPool(concurrency=N)

    start = time.monotonic()
    task_ids = [
        await pool.submit(
            cmd=["sleep", str(SLEEP_SECS)],
            jail_root=str(tmp_path),
            workdir=str(tmp_path),
            allowed_executables=["sleep"],
            timeout_secs=5.0,
        )
        for _ in range(N)
    ]

    results = await asyncio.gather(*(_wait_for(pool, tid) for tid in task_ids))
    elapsed = time.monotonic() - start

    assert all(r["status"] == "completed" for r in results)
    pids = {r["result"]["pid"] for r in results}
    assert len(pids) == N, "expected N distinct real PIDs, not a shared/fake identifier"
    # Real concurrency: N tasks at SLEEP_SECS each finish well under N * SLEEP_SECS
    # (which is what serial/fake dispatch would take).
    assert elapsed < SLEEP_SECS * (N / 2), (
        f"tasks appear to have run serially (elapsed={elapsed:.2f}s; "
        f"N*latency would be {SLEEP_SECS * N:.2f}s)"
    )


async def test_worker_pool_status_shows_real_pid_while_running(tmp_path):
    pool = _native.WorkerPool(concurrency=1)
    task_id = await pool.submit(
        cmd=["sleep", "0.3"],
        jail_root=str(tmp_path),
        workdir=str(tmp_path),
        allowed_executables=["sleep"],
        timeout_secs=5.0,
    )
    for _ in range(50):
        status = await pool.status(task_id)
        if status["status"] == "running":
            assert status["pid"] > 0
            return
        if status["status"] == "completed":
            break
        await asyncio.sleep(0.01)
    raise AssertionError("never observed a running status with a real pid")


async def test_unknown_task_id_returns_none(tmp_path):
    pool = _native.WorkerPool(concurrency=1)
    assert await pool.status("does-not-exist") is None
