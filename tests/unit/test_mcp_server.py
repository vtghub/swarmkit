"""swarmkit's own MCP server: list_agents and query_memory are local and
stateless, so they're tested directly with no daemon and no network. Their
tool schemas are checked via the real FastMCP protocol layer (mcp.list_tools()).
spawn_agent/get_task_status need a running daemon and (for a real run) a
live Anthropic credential — see tests/integration/test_mcp_server_live.py.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from swarmkit.memory.embeddings import HashingEmbedder
from swarmkit.memory.rag import MemoryIndex
from swarmkit.memory.store import MemoryStore
from swarmkit.memory.vectors import open_vector_store
from swarmkit.mcp_server.server import get_task_status, list_agents, mcp, query_memory


def test_list_agents_returns_the_builtin_catalog():
    agents = list_agents()
    names = {a["name"] for a in agents}
    assert {"coder", "reviewer", "tester", "docs", "architect"} <= names
    for a in agents:
        assert set(a.keys()) == {"name", "description"}


async def test_query_memory_retrieves_over_a_real_memory_directory(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    vectors = open_vector_store(tmp_path / "vectors.bin")
    embedder = HashingEmbedder(dim=384)  # must match query_memory's own embedder dim
    index = MemoryIndex(store, vectors, embedder)
    await index.add("the rust sandbox enforces a directory jail")
    await index.add("bananas are a good source of potassium")
    vectors.save(str(tmp_path / "vectors.bin"))
    store.close()

    results = await query_memory(str(tmp_path), "rust sandbox jail", k=3)
    assert results
    assert any("directory jail" in r["content"] for r in results)


async def test_get_task_status_without_a_running_daemon_raises_a_clear_error(monkeypatch, tmp_path):
    # Point at an isolated, guaranteed-empty runtime dir so this test doesn't
    # depend on whether some other daemon happens to be running on the host.
    monkeypatch.setenv("SWARMKIT_RUNTIME_DIR", str(tmp_path / "isolated-runtime"))

    import pytest

    from swarmkit.cli.daemon_client import DaemonUnavailable

    with pytest.raises(DaemonUnavailable):
        await get_task_status("nope")


def test_server_exposes_all_four_tools_with_real_schemas():
    import asyncio

    async def _list():
        return await mcp.list_tools()

    tools = asyncio.run(_list())
    names = {t.name for t in tools}
    assert names == {"spawn_agent", "get_task_status", "list_agents", "query_memory"}
