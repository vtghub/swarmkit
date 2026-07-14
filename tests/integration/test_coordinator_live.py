"""Live canary: full real decompose + concurrent dispatch through the
Anthropic API and the local sandbox — the Phase 3 'done' criteria exercised
end to end. Skipped without a real credential."""

from __future__ import annotations

import os

import pytest

from swarmkit.agents.catalog import AgentCatalog
from swarmkit.swarm.coordinator import Coordinator
from swarmkit.swarm.topology import Topology

pytestmark = pytest.mark.skipif(
    not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
    reason="requires a real Anthropic credential",
)


async def test_goal_decomposes_into_concurrent_quorum_verified_subtasks(tmp_path):
    catalog = AgentCatalog()
    coordinator = Coordinator(catalog, topology=Topology.STAR)

    goal = (
        "Create a file called ok.txt containing the word ok (assign this to "
        "the tester agent, with verify=true and verify_command set to "
        "`cat ok.txt`), and separately have the docs agent write a one "
        "sentence description of what a sandbox is in this project — leave "
        "that second subtask unverified (verify=false, verify_command=null)."
    )

    result = await coordinator.run(
        goal,
        jail_root=str(tmp_path),
        workdir=str(tmp_path),
        allowed_executables=["cat", "echo", "touch", "ls"],
    )

    assert len(result.subtasks) >= 2
    assert any(r.quorum is not None for r in result.results), (
        "expected at least one quorum-verified subtask"
    )
    for r in result.results:
        if r.quorum is not None:
            assert r.quorum.replica_results, "quorum verification never actually ran"
            assert r.success, f"quorum check failed: {r.quorum.votes}"
