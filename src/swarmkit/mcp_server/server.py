"""swarmkit's own MCP server: exposes real, backed tools to any MCP client
(Claude Code, another agent framework, a curious human with an MCP
inspector). Every tool here does what its schema claims:

- `spawn_agent` / `get_task_status` proxy to swarmkitd over the same Unix
  socket the CLI uses — the agent actually runs inside the daemon, dispatched
  through the real Rust worker pool for its tool calls.
- `list_agents` / `query_memory` are local and stateless (no daemon needed):
  reading the YAML agent catalog and running hybrid RRF+MMR retrieval over a
  memory directory are both fast, synchronous-enough operations that routing
  them through the daemon would only add latency for no benefit.

Uses the official `mcp` Python SDK's FastMCP — no protocol reimplementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from swarmkit.agents.catalog import AgentCatalog
from swarmkit.cli import daemon_client
from swarmkit.memory.embeddings import HashingEmbedder
from swarmkit.memory.rag import MemoryIndex
from swarmkit.memory.store import MemoryStore
from swarmkit.memory.vectors import open_vector_store

mcp = FastMCP(name="swarmkit")


@mcp.tool()
async def spawn_agent(
    agent_name: str,
    goal: str,
    jail_root: str,
    workdir: str,
    allowed_executables: list[str],
) -> dict[str, Any]:
    """Spawn a catalog agent (see list_agents) on a goal. Runs inside
    swarmkitd, dispatched through its real Rust worker pool. Returns a
    task_id immediately — poll get_task_status for the result."""
    task_id = await daemon_client.spawn_agent(
        agent_name,
        goal,
        jail_root=jail_root,
        workdir=workdir,
        allowed_executables=allowed_executables,
    )
    return {"task_id": task_id}


@mcp.tool()
async def get_task_status(task_id: str) -> dict[str, Any]:
    """Get the status of a task previously returned by spawn_agent."""
    status = await daemon_client.agent_task_status(task_id)
    if status is None:
        return {"status": "unknown"}
    return status


@mcp.tool()
def list_agents() -> list[dict[str, str]]:
    """List available catalog agents (name and description only)."""
    catalog = AgentCatalog()
    return [{"name": s.name, "description": s.description} for s in catalog.list_summaries()]


@mcp.tool()
async def query_memory(memory_dir: str, query: str, k: int = 5) -> list[dict[str, Any]]:
    """Hybrid (keyword + vector) retrieval over a swarmkit memory directory
    previously populated via the memory API (memory.db + vectors.bin)."""
    base = Path(memory_dir)
    store = MemoryStore(base / "memory.db")
    vectors = open_vector_store(base / "vectors.bin")
    embedder = HashingEmbedder(dim=384)
    index = MemoryIndex(store, vectors, embedder)
    try:
        results = await index.retrieve(query, k=k)
        return [{"content": r.record.content, "score": r.score} for r in results]
    finally:
        store.close()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
