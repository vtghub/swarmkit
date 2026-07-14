# swarmkit

A real, non-theater multi-agent orchestrator. Every capability below is
backed by a runnable test that observes an external side effect (a real
subprocess PID, a real Anthropic `request_id`, a measured byte size, a
measured wall-clock concurrency) — see `docs/PLAN.md` for the verification
approach. If a capability isn't listed here, it doesn't exist yet.

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

## Agent catalog

- architect: Plans an implementation approach for a non-trivial change before code is written.
- coder: Writes and edits code to implement a specific, well-scoped change.
- docs: Writes or updates documentation for a specific change or feature.
- reviewer: Reviews a diff or piece of code for correctness bugs, not style nits.
- tester: Runs a test suite or verification command and reports pass/fail with evidence.

Only name + description ever reach an LLM's context (a few hundred tokens total, regardless of catalog size) — full persona and tool schema load only at actual spawn time. Add an agent by dropping a YAML file into `agents/definitions/` or `~/.config/swarmkit/agents/`.

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

See `docs/PLAN.md` for the full build order, what's deferred, and how each
claim above is verified.
