"""dispatch_subtasks: N subtasks at concurrency N complete in ~max(latency),
not sum(latency) — the same concurrency proof pattern as the worker pool
(tests/unit/test_worker_pool.py), applied to the coordinator's own fan-out.
Uses a fake run_one (no network) so this test needs no Anthropic credential.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from swarmkit.swarm.coordinator import AgentRunResult, Subtask, SubtaskResult, dispatch_subtasks

N = 5
LATENCY_SECS = 0.3


def _subtask(i: int) -> Subtask:
    return Subtask(id=f"s{i}", agent="coder", goal=f"do thing {i}")


async def test_subtasks_run_concurrently_at_full_concurrency():
    async def run_one(subtask: Subtask) -> SubtaskResult:
        await asyncio.sleep(LATENCY_SECS)
        return SubtaskResult(
            subtask=subtask,
            run=AgentRunResult(request_id=None, text=subtask.id, input_tokens=0, output_tokens=0),
        )

    subtasks = [_subtask(i) for i in range(N)]
    start = time.monotonic()
    results = await dispatch_subtasks(subtasks, run_one, concurrency=N)
    elapsed = time.monotonic() - start

    assert [r.subtask.id for r in results] == [s.id for s in subtasks]
    assert elapsed < LATENCY_SECS * (N / 2), (
        f"subtasks appear to have run serially (elapsed={elapsed:.2f}s; "
        f"N*latency would be {LATENCY_SECS * N:.2f}s)"
    )


async def test_concurrency_cap_is_respected():
    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def run_one(subtask: Subtask) -> SubtaskResult:
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return SubtaskResult(
            subtask=subtask,
            run=AgentRunResult(request_id=None, text="", input_tokens=0, output_tokens=0),
        )

    subtasks = [_subtask(i) for i in range(N)]
    cap = 2
    await dispatch_subtasks(subtasks, run_one, concurrency=cap)
    assert max_in_flight <= cap


async def test_a_failing_subtask_propagates_as_an_exception():
    async def run_one(subtask: Subtask) -> SubtaskResult:
        if subtask.id == "s1":
            raise RuntimeError("boom")
        return SubtaskResult(
            subtask=subtask,
            run=AgentRunResult(request_id=None, text="", input_tokens=0, output_tokens=0),
        )

    subtasks = [_subtask(0), _subtask(1)]
    with pytest.raises(RuntimeError, match="boom"):
        await dispatch_subtasks(subtasks, run_one, concurrency=2)
