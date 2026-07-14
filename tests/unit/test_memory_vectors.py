"""VectorStore persistence via the memory/vectors.py wrapper, and the
bytes-per-entry benchmark at the Python-facing boundary — proves
store-then-retrieve survives a simulated process restart end to end, not
just at the Rust unit-test level."""

from __future__ import annotations

from swarmkit.memory.embeddings import HashingEmbedder
from swarmkit.memory.vectors import open_vector_store


def test_vector_store_survives_a_simulated_process_restart(tmp_path):
    path = tmp_path / "vectors.bin"
    embedder = HashingEmbedder(dim=384)

    store = open_vector_store(path)
    [vector] = embedder.embed(["the quick brown fox"])
    store.add("mem-1", vector)
    store.save(str(path))
    del store  # simulate process exit

    reopened = open_vector_store(path)
    assert len(reopened) == 1
    results = reopened.search(vector, k=1)
    assert results[0][0] == "mem-1"


def test_bytes_per_entry_is_low_single_digit_kb(tmp_path):
    path = tmp_path / "vectors.bin"
    embedder = HashingEmbedder(dim=384)
    store = open_vector_store(path)

    texts = [f"memory entry number {i} about swarmkit internals" for i in range(20)]
    for i, vector in enumerate(embedder.embed(texts)):
        store.add(f"mem-{i}", vector)
    store.save(str(path))

    bytes_per_entry = store.on_disk_bytes() / len(store)
    # Fixed-width binary format keeps this comfortably under 2KB/entry
    # regardless of entry count.
    assert bytes_per_entry < 2048, f"expected <2KB/entry, got {bytes_per_entry:.1f}"
