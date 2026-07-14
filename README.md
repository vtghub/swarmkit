# swarmkit

A multi-agent orchestration system for Claude Code/Codex-style workflows: agents, swarm coordination, memory/RAG, MCP client+server integration, an append-only audit log, and minimal cross-daemon federation — built on one rule: **no capability ships unless a test observes a real, external side effect proving it happened** (a real subprocess PID, a real Anthropic `request_id`, a measured byte size, measured wall-clock concurrency).

Every claim in this README is backed by a test or demo script named next to it. See the full architecture and build order in [`docs/PLAN.md`](docs/PLAN.md).

## Architecture at a glance

- **Rust core** (`crates/swarmkit-core`, bound into Python via PyO3/`maturin` as `swarmkit._native`): the worker pool, task queue, sandboxed subprocess execution, and the compact vector store — i.e. everything where "is this actually happening" needs to be a compiled guarantee, not a convention.
- **Python orchestration** (`src/swarmkit`): CLI, agent definitions, LLM provider calls (Anthropic), swarm coordination logic, MCP protocol glue, and SQLite-backed structured memory.

## Features

The capability descriptions below are generated from `src/swarmkit/docs/generate.py`
— the same source used for `swarmkit docs generate`'s `AGENTS.md`/`CLAUDE.md`
output — so a capability is described once, not hand-duplicated across
README.md, docs/PLAN.md, and the generated project docs. Run
`python scripts/sync_docs.py` after changing a description there.

<!-- swarmkit:generated-features:start -->

## Golden path

```
swarmkit init                          # checks ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN
swarmkit daemon start                  # background daemon owning the real Rust worker pool
swarmkit run "<goal>"                  # one agent, tool calls sandboxed via the daemon
swarmkit swarm run "<goal>"            # decompose into concurrent, quorum-verified subtasks
swarmkit status                        # real task/PID info, sourced from Rust
swarmkit audit                         # every tool call + provider request, append-only
swarmkit daemon stop
```

## Swarm coordination

`swarmkit swarm run "<goal>" --topology star|mesh` decomposes a goal into
subtasks via the Anthropic API's structured output, then dispatches them
concurrently through swarmkitd's Rust worker pool. `star` has no concurrency
cap; `mesh` caps fan-out at 8 concurrent agents (a real enforced limit, not
a marketing number). Subtasks with a `verify_command` are quorum-verified:
re-run across 3 independent replicas, accepted only on majority agreement —
a real reliability mechanism, not an unverifiable Raft/Byzantine/Gossip
claim. Full Raft/Byzantine/Gossip consensus is explicitly out of scope for
v1.

## Memory / RAG

SQLite (`memory.db`) + FTS5 for keyword search, a compact Rust-backed
vector store (`vectors.bin`, fixed-width binary format, lazy `instant-distance`
HNSW) for semantic search. Retrieval combines both via Reciprocal Rank
Fusion, then re-ranks with MMR for diversity. Measured at ~1KB/entry on
disk, tracked by a CI benchmark so storage growth doesn't silently regress.
Embeddings are pluggable: a dependency-free `HashingEmbedder` by default, or
`SentenceTransformerEmbedder` (`pip install 'swarmkit[embeddings]'`) for
real semantic quality.

## MCP integration

`swarmkit mcp serve` exposes swarmkit's own tools over stdio:
- `spawn_agent` / `get_task_status` — proxy to a real daemon RPC; the agent
  runs inside swarmkitd, its tool calls dispatched through the Rust worker
  pool.
- `list_agents` — the same name+description-only catalog view above.
- `query_memory` — hybrid RRF+MMR retrieval over a memory directory.

`swarmkit mcp list-tools <command> [args...]` inspects any external stdio
MCP server. Agents can also pull tools from an external MCP server directly
into their tool loop (`Agent.run(..., extra_tools=...)`) — verified against
a real third-party MCP server binary, not just a protocol mock.

## Security & federation

Every sandboxed subprocess execution and every Anthropic provider request is
recorded in an append-only audit log (`swarmkit audit`) — `UPDATE`/`DELETE`
are rejected by SQLite triggers at the engine level, not just by API
convention. Stdout/stderr are redacted for API keys, tokens, and private-key
blocks before they're ever written.

Federation is minimal but real: each daemon generates and persists its own
ed25519 keypair (`swarmkit identity`); peers are registered explicitly
(`swarmkit peer add <name> <host> <port> <pubkey>`, exchanged out-of-band —
no auto-discovery, no trust-on-first-use). Daemon-to-daemon task requests
are signed over their canonical JSON payload and verified against the
sender's registered key before anything reaches the worker pool. Full mTLS
and formal compliance certifications are explicitly out of scope for v1.

<!-- swarmkit:generated-features:end -->

## Status

Each phase's implementation, the tests that prove it, and what's explicitly
deferred are tracked in [`docs/PLAN.md`](docs/PLAN.md#build-order):

- **Phase 0** (done): single real agent — Anthropic call + Rust-sandboxed subprocess tool execution.
- **Phase 1** (done): `swarmkitd` background daemon + real Rust worker pool; N tasks at concurrency N complete in ~max(latency), not sum — `tests/unit/test_worker_pool.py`.
- **Phase 2** (done): memory/RAG; measured ~1KB/entry on disk, tracked by a CI benchmark — `tests/unit/test_memory_vectors.py`.
- **Phase 3** (done): swarm coordination + lazy agent catalog — `tests/unit/test_agent_catalog.py`.
- **Phase 4** (done): MCP integration, both directions, verified against the real `vtghub/mcp-native-core` binary.
- **Phase 5** (done, v1 complete): security hardening + minimal federation, verified against two real `swarmkitd` OS processes — `tests/integration/test_federation_lifecycle.py`.
- **Docs generation** (done): `swarmkit docs generate` emits the lean `AGENTS.md`/`CLAUDE.md` above — `tests/unit/test_docs_generate.py`.

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
