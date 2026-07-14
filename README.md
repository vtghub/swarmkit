# swarmkit

A multi-agent orchestration system for Claude Code/Codex-style workflows — agents, swarms, memory/RAG, and MCP tool integration — built on one rule: **no capability ships unless a test observes a real, external side effect proving it happened.**

## Why this exists

[Ruflo](https://github.com/ruvnet/ruflo) (formerly `claude-flow`) popularized the "agent meta-harness" idea, but an independent audit and its own issue tracker found the implementation didn't match the pitch:

- ~290 of ~300 advertised MCP tools write a JSON record and execute nothing ([audit](https://gist.github.com/roman-rr/ed603b676af019b8740423d2bb8e4bf6)).
- `agent_spawn` doesn't fork a real subprocess/worker — it registers state.
- 106 agent definitions load into context by default (~300K tokens), most referencing MCP servers that don't exist in a standard install ([#1504](https://github.com/ruvnet/ruflo/issues/1504)).
- The memory/graph layer uses ~100MB of storage for 20 entries.
- Onboarding is a paradox of choice across install paths and commands ([#1196](https://github.com/ruvnet/ruflo/issues/1196)).

swarmkit is an attempt at the same feature surface — agents, swarm coordination, memory/RAG, MCP client+server, minimal federation, security guardrails — built so each capability is verifiably real rather than assumed. See the architecture and build order in [`docs/PLAN.md`](docs/PLAN.md).

## Architecture at a glance

- **Rust core** (`crates/swarmkit-core`, bound into Python via PyO3/`maturin` as `swarmkit._native`): the worker pool, task queue, sandboxed subprocess execution, and the compact vector store — i.e. everything where "is this actually happening" needs to be a compiled guarantee, not a convention.
- **Python orchestration** (`src/swarmkit`): CLI, agent definitions, LLM provider calls (Anthropic), swarm coordination logic, MCP protocol glue, and SQLite-backed structured memory.

## Status

- **Phase 0** (done): a single real agent — a real Anthropic call whose tool use runs as a real Rust-sandboxed subprocess.
- **Phase 1** (done): `swarmkitd`, a background daemon owning a real Rust worker pool (`crates/swarmkit-core/src/{worker_pool,taskqueue}.rs`). `swarmkit run` now dispatches tool execution through the daemon over a Unix domain socket instead of running the subprocess in-process; `swarmkit daemon start|stop|status` and `swarmkit status` manage and inspect it. N tasks submitted at worker-pool concurrency N complete in ~max(latency), not sum(latency) — see `tests/unit/test_worker_pool.py`.
- **Phase 2** (done): memory/RAG. `crates/swarmkit-core/src/vectors.rs` is a compact vector store (fixed-width binary format, `instant-distance` HNSW rebuilt lazily from it) exposed as `swarmkit._native.VectorStore`; `src/swarmkit/memory/store.py` is SQLite + FTS5 for text/keyword search; `src/swarmkit/memory/rag.py` combines both via Reciprocal Rank Fusion, then re-ranks with MMR for diversity. Measured ~1KB/entry on disk (`tests/unit/test_memory_vectors.py`), vs. Ruflo's reported ~5MB/entry — roughly 4,700x smaller, not just "10x". Embeddings are pluggable (`src/swarmkit/memory/embeddings.py`): a dependency-free `HashingEmbedder` for tests/offline use, and an optional `SentenceTransformerEmbedder` (`pip install 'swarmkit[embeddings]'`) for real semantic quality.

Nothing here should be assumed to work until its corresponding test/demo script is green; see `docs/PLAN.md` for the phase-by-phase build order and what "done" means for each remaining phase.

## Development

Requires Rust (stable) and Python 3.11+.

```bash
# build the native extension into your active Python env
pip install maturin
maturin develop -m crates/swarmkit-py/Cargo.toml

# run the Phase 0 demo (requires ANTHROPIC_API_KEY)
python scripts/demo_single_agent.py

# Phase 1: run a goal through the daemon-mediated worker pool
swarmkit daemon start
swarmkit run "list the files in this directory"
swarmkit status
swarmkit daemon stop

# Phase 2: memory/RAG demo (no API key or ML download needed)
python scripts/demo_memory.py
```

## License

MIT — see [`LICENSE`](LICENSE).
