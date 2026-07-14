"""Quorum/majority-vote verification: correctness (majority wins, ties/
disagreement rejected) and a concurrency proof that the replicas actually run
in parallel through the real Rust worker pool, not one after another."""

from __future__ import annotations

import asyncio
import time

from swarmkit._native import WorkerPool
from swarmkit.swarm.consensus import DEFAULT_REPLICAS, quorum_execute


async def test_unanimous_agreement_is_accepted():
    async def run_once() -> dict:
        return {"exit_code": 0, "stdout": "ok"}

    result = await quorum_execute(run_once, replicas=3)
    assert result.accepted
    assert result.result == {"exit_code": 0, "stdout": "ok"}


async def test_majority_agreement_is_accepted_over_a_minority_dissent():
    calls = {"n": 0}

    async def run_once() -> dict:
        calls["n"] += 1
        # Two replicas agree, one is flaky/different.
        if calls["n"] == 2:
            return {"exit_code": 1, "stdout": "flaky failure"}
        return {"exit_code": 0, "stdout": "ok"}

    result = await quorum_execute(run_once, replicas=3)
    assert result.accepted
    assert result.result["exit_code"] == 0


async def test_no_majority_is_rejected():
    calls = {"n": 0}
    outcomes = [
        {"exit_code": 0, "stdout": "a"},
        {"exit_code": 1, "stdout": "b"},
        {"exit_code": 2, "stdout": "c"},
    ]

    async def run_once() -> dict:
        outcome = outcomes[calls["n"]]
        calls["n"] += 1
        return outcome

    result = await quorum_execute(run_once, replicas=3)
    assert not result.accepted
    assert result.result is None
    assert len(result.replica_results) == 3


async def test_replicas_run_concurrently_through_the_real_worker_pool(tmp_path):
    """The point of quorum verification is to catch flakiness cheaply — which
    only works if the replicas actually run at once. This proves it using the
    real Rust worker pool, the same dispatch path the daemon uses."""
    pool = WorkerPool(concurrency=DEFAULT_REPLICAS)
    sleep_secs = 0.4

    async def run_once() -> dict:
        task_id = await pool.submit(
            cmd=["sleep", str(sleep_secs)],
            jail_root=str(tmp_path),
            workdir=str(tmp_path),
            allowed_executables=["sleep"],
            timeout_secs=5.0,
        )
        while True:
            status = await pool.status(task_id)
            if status["status"] == "completed":
                return status["result"]
            if status["status"] == "failed":
                raise RuntimeError(status["error"])
            await asyncio.sleep(0.02)

    start = time.monotonic()
    result = await quorum_execute(run_once, replicas=DEFAULT_REPLICAS)
    elapsed = time.monotonic() - start

    assert result.accepted
    assert elapsed < sleep_secs * (DEFAULT_REPLICAS / 2), (
        f"replicas appear to have run serially (elapsed={elapsed:.2f}s; "
        f"replicas*latency would be {sleep_secs * DEFAULT_REPLICAS:.2f}s)"
    )
