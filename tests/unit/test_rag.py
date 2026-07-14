"""Hybrid retrieval (RRF + MMR) over a real MemoryStore + real VectorStore,
using the dependency-free HashingEmbedder so this test needs no ML download."""

from __future__ import annotations

from swarmkit._native import VectorStore
from swarmkit.memory.embeddings import HashingEmbedder
from swarmkit.memory.rag import MemoryIndex
from swarmkit.memory.store import MemoryStore


async def test_retrieve_surfaces_relevant_memories_over_irrelevant_ones(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    vectors = VectorStore()
    embedder = HashingEmbedder(dim=128)
    index = MemoryIndex(store, vectors, embedder)

    await index.add("swarmkit runs agent tool calls as real sandboxed subprocesses")
    await index.add("the rust worker pool dispatches jobs concurrently")
    await index.add("bananas are a good source of potassium")

    # HashingEmbedder is lexical (bag-of-words), not semantic, and FTS5 has no
    # stemming — so the query must share literal tokens with the target
    # content for either retrieval path to find it, same as it would for any
    # keyword-based system.
    results = await index.retrieve("sandboxed subprocesses", k=2)
    assert results, "expected at least one retrieved memory"
    top_contents = [r.record.content for r in results]
    assert any("sandboxed subprocesses" in c for c in top_contents)
    assert all("bananas" not in c for c in top_contents)
    store.close()


async def test_mmr_avoids_selecting_two_near_duplicate_results(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    vectors = VectorStore()
    embedder = HashingEmbedder(dim=128)
    index = MemoryIndex(store, vectors, embedder)

    # Two near-duplicates plus one distinct memory, all relevant to the query.
    await index.add("the rust sandbox enforces a directory jail")
    await index.add("the rust sandbox enforces a directory jail for commands")
    await index.add("the rust sandbox also enforces wall-clock timeouts")

    results = await index.retrieve("rust sandbox jail", k=2, lambda_mult=0.3)
    assert len(results) == 2
    contents = {r.record.content for r in results}
    # With heavy diversity weighting, the two near-identical entries shouldn't
    # both be selected ahead of the distinct third one.
    assert not (
        "the rust sandbox enforces a directory jail" in contents
        and "the rust sandbox enforces a directory jail for commands" in contents
    )
    store.close()
