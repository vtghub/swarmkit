#!/usr/bin/env python3
"""Phase 2 smoke test: add memories, simulate a process restart, retrieve via
hybrid RRF+MMR search. No ANTHROPIC_API_KEY or ML download required — uses the
dependency-free HashingEmbedder.

    python scripts/demo_memory.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from swarmkit.memory.embeddings import HashingEmbedder
from swarmkit.memory.rag import MemoryIndex
from swarmkit.memory.store import MemoryStore
from swarmkit.memory.vectors import open_vector_store


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "memory.db"
        vec_path = Path(tmp) / "vectors.bin"
        embedder = HashingEmbedder(dim=256)

        store = MemoryStore(db_path)
        vectors = open_vector_store(vec_path)
        index = MemoryIndex(store, vectors, embedder)

        await index.add("swarmkit's sandbox enforces a directory jail and a wall-clock timeout")
        await index.add("the rust worker pool dispatches sandboxed subprocess jobs concurrently")
        await index.add("bananas are a good source of potassium")

        vectors.save(str(vec_path))
        bytes_per_entry = vectors.on_disk_bytes() / len(vectors)
        print(f"stored 3 memories; vector store is {vectors.on_disk_bytes()} bytes "
              f"({bytes_per_entry:.1f} bytes/entry, vs. Ruflo's reported ~5,000,000)")
        store.close()

        # --- simulate a process restart: fresh objects, same files on disk ---
        store = MemoryStore(db_path)
        vectors = open_vector_store(vec_path)
        index = MemoryIndex(store, vectors, embedder)

        results = await index.retrieve("sandboxed subprocess jail", k=2)
        print("\n--- retrieved after simulated restart ---")
        for r in results:
            print(f"[{r.score:.4f}] {r.record.content}")

        assert results, "no memories retrieved after restart"
        assert any("directory jail" in r.record.content for r in results)
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
