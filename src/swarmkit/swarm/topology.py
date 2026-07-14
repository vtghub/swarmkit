"""Swarm topology: how subtasks are distributed across agent instances.

STAR: one coordinator decomposes a goal into a subtask DAG and dispatches
each subtask directly. This is the only topology with full behavior in
Phase 3.

MESH: same coordinator-driven dispatch, but fan-out is capped at
MESH_MAX_PEERS concurrent agents — a real, enforced constraint (see
`max_concurrent_agents` and its test). True peer-to-peer delegation (an
agent handing a subtask directly to another peer agent, without going back
through the coordinator) is not implemented — claiming it here would be
exactly the kind of overclaim this project exists to avoid. For now MESH
differs from STAR only by that concurrency cap.
"""

from __future__ import annotations

from enum import Enum

MESH_MAX_PEERS = 8


class Topology(str, Enum):
    STAR = "star"
    MESH = "mesh"


def max_concurrent_agents(topology: Topology, requested: int) -> int:
    """The actual dispatch concurrency for `requested` subtasks under `topology`."""
    if requested < 1:
        return 0
    if topology == Topology.MESH:
        return min(requested, MESH_MAX_PEERS)
    return requested
