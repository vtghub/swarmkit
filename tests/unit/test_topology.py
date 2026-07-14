from __future__ import annotations

from swarmkit.swarm.topology import MESH_MAX_PEERS, Topology, max_concurrent_agents


def test_star_topology_has_no_extra_cap():
    assert max_concurrent_agents(Topology.STAR, 3) == 3
    assert max_concurrent_agents(Topology.STAR, 20) == 20


def test_mesh_topology_caps_at_max_peers():
    assert max_concurrent_agents(Topology.MESH, 3) == 3
    assert max_concurrent_agents(Topology.MESH, MESH_MAX_PEERS) == MESH_MAX_PEERS
    assert max_concurrent_agents(Topology.MESH, MESH_MAX_PEERS + 5) == MESH_MAX_PEERS


def test_zero_or_fewer_requested_yields_zero():
    assert max_concurrent_agents(Topology.STAR, 0) == 0
    assert max_concurrent_agents(Topology.MESH, 0) == 0
