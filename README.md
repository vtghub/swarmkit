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
- **Phase 3** (done): swarm coordination. `swarmkit swarm run "<goal>"` decomposes a goal into subtasks via the Anthropic API's structured output (`src/swarmkit/swarm/coordinator.py`), dispatches them concurrently to catalog agents (`src/swarmkit/agents/catalog.py` — 5 starter agents in `agents/definitions/*.yaml`: coder, reviewer, tester, docs, architect) through swarmkitd's Rust worker pool, and quorum-verifies (`src/swarmkit/swarm/consensus.py`) any subtask with a `verify_command` by re-running it across 3 independent replicas, accepting only on majority agreement — a real reliability mechanism instead of an unverifiable Raft/Byzantine/Gossip claim. The coordinator's context only ever sees agent name + description (a few hundred tokens, structurally incapable of carrying a full system prompt — see `tests/unit/test_agent_catalog.py`), the direct fix for Ruflo's ~300K-token default agent-catalog bloat.
- **Phase 4** (done): MCP integration, both directions. `swarmkit mcp serve` runs swarmkit's own MCP server (`src/swarmkit/mcp_server/server.py`, official `mcp` SDK's `FastMCP`) exposing `spawn_agent`/`get_task_status` (proxy to a real daemon RPC — the agent runs inside swarmkitd, dispatched through the Rust worker pool) and `list_agents`/`query_memory` (local and stateless, no daemon needed). `src/swarmkit/mcp_server/client_tools.py` makes swarmkit an MCP *client* too: `Agent.run(..., extra_tools=...)` can pull in tools from any external stdio MCP server. This was verified against the real `vtghub/mcp-native-core` binary (built from source and driven over the actual MCP wire protocol) with zero swarmkit-side special-casing. `swarmkit mcp list-tools <command>` is a diagnostic for inspecting any such server. No dedicated Rust module was added for this phase — by Phase 4 the "hot path" already ran through the existing worker pool and vector store, so a pass-through file would have been pure indirection (see `docs/PLAN.md`'s scope note).
- **Phase 5** (done, v1 complete): security hardening + minimal federation. `src/swarmkit/security/secrets.py` redacts API keys/tokens/private-key blocks from anything written to the audit log; `src/swarmkit/security/audit.py` is an append-only SQLite log — `UPDATE`/`DELETE` are rejected by triggers at the SQLite engine level itself, not just by this module's API, and both swarmkitd and any CLI-hosted run (`swarmkit run`, `swarm run`) write to the same file directly (`swarmkit audit` reads it back, no running daemon required). Federation is deliberately minimal-but-real: `src/swarmkit/federation/identity.py` generates an ed25519 keypair per daemon on first run (persisted, survives restart) and an explicit `PeerRegistry` — no auto-discovery, no trust-on-first-use; a peer exists only because `swarmkit peer add <name> <host> <port> <pubkey>` was run with a key exchanged out-of-band. `src/swarmkit/federation/transport.py` is a Starlette+uvicorn HTTP listener where every request is signed over its canonical JSON payload and verified against the sender's registered key *before* it ever reaches the Rust worker pool — an unsigned, forged, or unregistered-peer request never spawns a subprocess. Verified with two real `swarmkitd` OS processes completing a signed cross-daemon task end to end (`tests/integration/test_federation_lifecycle.py`), plus the disallowed-command-blocked-pre-execution guarantee re-checked over the federation path itself (on top of the Rust-level proof that's been in place since Phase 0, `sandbox::tests::non_allowlisted_command_is_rejected_before_spawn`).

- **Docs generation** (done): `swarmkit docs generate [--dir .]` (`src/swarmkit/docs/generate.py`) emits a lean `AGENTS.md`/`CLAUDE.md` for a swarmkit-using project. The one section that scales with catalog size — the agent list — is rendered from the live `AgentCatalog.render_summary()`, the same name+description-only view the coordinator itself sees, so it structurally cannot balloon into Ruflo's reported ~300K-token default agent doc no matter how many (or how bloated) agent definitions exist. Everything else is a short, current description of the actual v1 feature surface below, not aspirational prose (`tests/unit/test_docs_generate.py`).

Nothing here should be assumed to work until its corresponding test/demo script is green; see `docs/PLAN.md` for the phase-by-phase build order and what "done" means for each phase.

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

# Phase 3: decompose a goal into concurrent, quorum-verified subtasks
swarmkit swarm run "add a one-line README note and verify it with cat README.md" --topology star

# Phase 4: run swarmkit's own MCP server, or inspect an external one
swarmkit mcp serve
swarmkit mcp list-tools /path/to/some/other/mcp-server

# Phase 5: audit log + two daemons federating a signed cross-daemon task
swarmkit audit --limit 10

swarmkit identity                                        # -> pubkey A
SWARMKIT_RUNTIME_DIR=/tmp/peer-b swarmkit identity        # -> pubkey B
swarmkit peer add peer-b 127.0.0.1 9002 <pubkey B>
SWARMKIT_RUNTIME_DIR=/tmp/peer-b swarmkit peer add peer-a 127.0.0.1 9001 <pubkey A>
swarmkit daemon start --federation-port 9001
SWARMKIT_RUNTIME_DIR=/tmp/peer-b swarmkit daemon start --federation-port 9002

# Docs: emit a lean AGENTS.md/CLAUDE.md reflecting the real v1 feature surface
swarmkit docs generate --dir .
```

## License

MIT — see [`LICENSE`](LICENSE).
