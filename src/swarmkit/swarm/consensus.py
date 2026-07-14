"""Quorum/majority-vote verification for correctness-sensitive subtasks.

For a single-daemon swarm, a Raft/Byzantine/Gossip consensus claim would be
unverifiable at this scale. Instead: a correctness-sensitive subtask's
verification command (e.g. "run the test suite") is dispatched to N
independent replicas concurrently, and accepted only on strict-majority
agreement over (exit_code, stdout). This catches flaky/nondeterministic
results — a real reliability benefit — without pretending to solve
distributed leader election.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

DEFAULT_REPLICAS = 3


@dataclass
class QuorumResult:
    accepted: bool
    result: dict[str, Any] | None
    votes: dict[str, int]
    replica_results: list[dict[str, Any]] = field(default_factory=list)


def _signature(result: dict[str, Any]) -> str:
    """Canonical signature two independent runs should agree on: exit code +
    stdout. Deliberately excludes pid/duration_ms, which are expected to
    differ run to run even when the command's actual result is identical."""
    return json.dumps(
        {"exit_code": result.get("exit_code"), "stdout": result.get("stdout")},
        sort_keys=True,
    )


async def quorum_execute(
    run_once: Callable[[], Awaitable[dict[str, Any]]],
    *,
    replicas: int = DEFAULT_REPLICAS,
) -> QuorumResult:
    """Run `run_once` `replicas` times concurrently (real concurrency — see
    tests/unit/test_consensus.py) and accept the result only if a strict
    majority of replicas produced the same (exit_code, stdout)."""
    if replicas < 1:
        raise ValueError("replicas must be >= 1")

    replica_results = list(await asyncio.gather(*(run_once() for _ in range(replicas))))
    votes = Counter(_signature(r) for r in replica_results)
    signature, count = votes.most_common(1)[0]
    majority_needed = replicas // 2 + 1
    accepted = count >= majority_needed
    winning_result = (
        next(r for r in replica_results if _signature(r) == signature) if accepted else None
    )
    return QuorumResult(
        accepted=accepted,
        result=winning_result,
        votes=dict(votes),
        replica_results=replica_results,
    )
