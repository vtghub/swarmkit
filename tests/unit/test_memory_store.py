"""MemoryStore: durable text storage + FTS5 keyword search, and persistence
across a simulated process restart (close + reopen against the same file)."""

from __future__ import annotations

from swarmkit.memory.store import MemoryStore


async def test_add_and_get_round_trip(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    record = await store.add("the quick brown fox jumps over the lazy dog")
    fetched = await store.get(record.id)
    assert fetched is not None
    assert fetched.content == record.content
    store.close()


async def test_keyword_search_ranks_relevant_hits_first(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    await store.add("swarmkit uses a real Rust sandbox for subprocess execution")
    await store.add("the weather today is sunny with a light breeze")
    await store.add("Rust and tokio power the swarmkit worker pool")

    hits = await store.keyword_search("rust sandbox", k=5)
    assert hits, "expected at least one keyword hit"
    top_record, _rank = hits[0]
    assert "rust" in top_record.content.lower() or "sandbox" in top_record.content.lower()
    store.close()


async def test_data_survives_a_simulated_process_restart(tmp_path):
    db_path = tmp_path / "memory.db"
    store = MemoryStore(db_path)
    record = await store.add("this memory must survive a restart")
    store.close()  # simulate process exit

    # Simulate a fresh process: a brand new MemoryStore instance, same file.
    reopened = MemoryStore(db_path)
    fetched = await reopened.get(record.id)
    assert fetched is not None
    assert fetched.content == "this memory must survive a restart"

    hits = await reopened.keyword_search("survive restart", k=5)
    assert any(r.id == record.id for r, _rank in hits)
    reopened.close()
