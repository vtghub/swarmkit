# swarmkit — a Ruflo alternative that doesn't fake it

## Context

The goal is a system "like Ruflo" (github.com/ruvnet/ruflo, formerly `claude-flow`) — a 64k-star "agent meta-harness" that wraps Claude Code/Codex with multi-agent swarms, adaptive memory/RAG, MCP tool integration, and cross-machine federation — but with better features and better performance.

Research into Ruflo surfaced specific, documented flaws rather than vague competition:
- **"99% theater"**: an independent audit of ~300 MCP tools found only ~10 do real work; ~290 write a JSON record and execute nothing.
- **Fake concurrency**: `agent_spawn` doesn't fork a real subprocess/worker — it registers state; execution still routes through Claude Code's own Task tool.
- **Context bloat**: 106 agent definition files load by default (~300K tokens), most referencing MCP servers that don't exist in a standard install.
- **Memory bloat**: ~100MB of storage for just 20 memory entries.
- **Onboarding confusion**: multiple install paths and a large command surface overwhelm new users.
- **Security debt**: multiple CVE'd dependencies pinned via overrides instead of fixed.

Full parity with Ruflo's feature surface (agents, swarms, memory/RAG, MCP, federation, security) is the goal, implemented as a **Rust core with a Python wrapper** — mirroring Ruflo's own Rust-engine-plus-plugins split, but with Python (not TypeScript) as the orchestration layer. Project name: **`swarmkit`**, license: **MIT**.

There is also a planned integration with `vtghub/mcp-native-core` as an upstream MCP server ("for faster search and faster parser"), to be wired in once its real tool schemas are inspected (see Phase 0 follow-up below).

The guiding principle for the whole build: **no capability ships unless a test observes a real, external side effect proving it happened** (a subprocess PID, an Anthropic `request_id`, a measured byte-size, measured wall-clock concurrency). Where Ruflo's marketed feature is unverifiable at this scale (Byzantine consensus, HIPAA/SOC2/GDPR "compliance modes", 106 agents, entity-graph+trajectory RAG), the plan builds a smaller, honestly-scoped, real version instead and flags what's deferred.

## Features

Generated from `src/swarmkit/docs/generate.py` (also the source for `swarmkit docs generate`'s `AGENTS.md`/`CLAUDE.md` output and README.md's Features section) — a single source of truth for capability descriptions instead of hand-duplicating them here, in README.md, and in the generated project docs. Run `python scripts/sync_docs.py` after changing a description there. The Architecture section below covers *why* each of these was built this way (language split, deferred scope, phase-by-phase implementation notes) rather than restating *what* it does.

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
disk — orders of magnitude smaller than Ruflo's reported ~5MB/entry.
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

## Architecture

**Language split**: mirroring Ruflo's own Rust-engine-plus-plugins design, everything performance-sensitive is a Rust core (`crates/swarmkit-core`) exposed to Python via `PyO3`/`maturin` as a native extension module (`swarmkit._native`); Python owns orchestration, LLM calls, and protocol glue where latency doesn't matter and iteration speed does. Concretely:
- **Rust**: worker pool + task queue (`swarm/worker_pool`, `swarm/taskqueue`), subprocess/sandbox execution (`security/sandbox`), the vector store and HNSW indexing (`memory/vectors`), and the hot tool-execution path inside the MCP server (`mcp_server/tools` — the actual dispatch/validation loop, not the protocol layer).
- **Python**: CLI (`cli/`), agent definitions (YAML + system prompts, `agents/`), the swarm coordinator's LLM-driven decomposition logic (`swarm/coordinator`), MCP protocol glue (`mcp_server/server`, using the native tool-execution module underneath), and SQLite-backed structured storage (`memory/store`).
- Rust functions are called from Python as ordinary awaitables (PyO3 `future_into_py` bridging Rust `tokio` tasks into the Python `asyncio` loop), so `swarmkit status` still shows one coherent task/PID view regardless of which side did the work.

**Process model**: `swarmkit` CLI is a thin client talking to a long-lived `swarmkitd` daemon over a Unix domain socket. The daemon's Python side owns the event loop and RPC surface; the Rust core owns the actual worker pool, task queue, and subprocess supervision underneath it. Subprocess-bound tool execution (bash/file ops/git/tests) is spawned and resource-limited by Rust (`tokio::process` + a directory-jail check) rather than Python's `asyncio.create_subprocess_exec`, since this is exactly the code path Ruflo faked (`agent_spawn` registering JSON instead of forking real work) — putting it in Rust makes "real PID, real resource limits" a compiled guarantee, not just a convention. `swarmkit status` can show the actual OS PID for any "spawned" agent.

**Provider layer**: `core/providers/base.py` defines a `Provider` ABC (`complete`, `stream`, `count_tokens`); `core/providers/anthropic_provider.py` wraps `anthropic.AsyncAnthropic`. This stays Python — it's I/O-bound network traffic, not compute, so Rust buys nothing here and Python keeps the Anthropic SDK's native tool-runner ergonomics. Per-role model defaults: coordinator/architect/security → `claude-opus-4-8`; bulk specialists (coder/reviewer/tester/docs) → `claude-sonnet-5`; high-volume cheap tasks → `claude-haiku-4-5`. Use `thinking={"type":"adaptive"}` + `output_config.effort`. Use the Anthropic Tool Runner for single-agent loops; a manual loop for the swarm coordinator (it needs to intercept `tool_use` blocks to route them as subtask assignments). Token counting always via `client.messages.count_tokens`, never `tiktoken`. Prompt caching: fixed `tools→system→messages` render order with `cache_control` on each agent's stable persona+tool block.

**MCP integration** *(implemented Phase 4 — see below for what shipped vs. this original plan)*: `mcp_server/server.py` uses the official `mcp` Python SDK's `FastMCP` for protocol handling. Two of its four tools (`spawn_agent`, `get_task_status`) proxy to a real daemon RPC — the agent itself runs inside swarmkitd, with its own tool calls dispatched through the real Rust worker pool. The other two (`list_agents`, `query_memory`) turned out not to need the daemon at all: reading the YAML catalog and running hybrid RRF+MMR retrieval are both local, stateless, and already backed by the Rust vector store from Phase 2 — routing them through a daemon round-trip would add latency for no benefit, so they don't. `swarmkit` is also an MCP *client*: `mcp_server/client_tools.py` uses the official `mcp` SDK plus the Anthropic SDK's MCP helpers (`anthropic.lib.tools.mcp.async_mcp_tool`) to pull tools from third-party MCP servers directly into an agent's tool loop (`Agent.run(..., extra_tools=...)`). This was verified against the real `vtghub/mcp-native-core` binary (built and exercised over the actual MCP wire protocol while implementing this phase) with zero swarmkit-side special-casing — any protocol-compliant stdio server works the same way.

*Scope note: no `mcp_tool_exec.rs` was added.* The original plan called for a dedicated Rust module for the MCP server's "hot dispatch/validation path," but by Phase 4 that path already ran through existing Rust modules end to end — `spawn_agent`/`get_task_status` through `worker_pool.rs`/`taskqueue.rs`, `query_memory` through `vectors.rs`. A pass-through Rust file that just re-forwarded to those modules would have been indirection with no real work of its own — exactly the kind of theater this project exists to avoid — so it was left out.

**Swarm coordination** *(current behavior: see ## Features)*: two topologies — `star` (one coordinator decomposes a goal into a subtask DAG via structured/JSON-schema output) and a fan-out-capped `mesh`. Task distribution runs through the Rust work-stealing queue. For consensus, skip pretending to implement Raft/Byzantine/Gossip on a single daemon — quorum/majority-vote verification stands in instead. Once federation needs cross-daemon leader election (not required for v1 — quorum verification alone has sufficed), use a simple time-boxed **leader-lease + heartbeat** model, not a from-scratch distributed consensus algorithm.

**Memory/RAG** *(current behavior: see ## Features)*: SQLite for structured storage (`tasks`, `agent_runs`, `tool_calls`, `entities`, `memories`) stays Python (`memory/store.py`, simple glue over `sqlite3`/`aiosqlite`). Vectors move to Rust: an in-process HNSW implementation (`instant-distance`) backing `memory/vectors`, persisted to disk and called from Python via the native module — this is the layer most exposed to Ruflo's "100MB for 20 entries" bloat, so a compact Rust-side binary format (fixed-width float arrays, no per-entry JSON/object overhead) is the direct fix. `faiss-cpu` deliberately skipped — heavier, GPU-oriented, and redundant once Rust owns the vector index. Embeddings inference (`sentence-transformers`, `all-MiniLM-L6-v2`) stays Python for now since it's the one unavoidable non-Anthropic ML dependency and ONNX/Rust-native embedding is an optimization to revisit later, not a Phase 0–5 requirement. A CI-tracked benchmark asserts bytes-per-memory-entry stays in the low-single-digit-KB range (vs. Ruflo's ~100MB/20 entries).

**Agent catalog** *(current behavior: see ## Features)*: 5 starter agents (`coder`, `reviewer`, `tester`, `docs`, `architect`), each a small YAML file (`name`, `description`, `system_prompt`, `allowed_tools`, `default_model`, `default_effort`). This is the direct, testable fix for Ruflo's ~300K-token default bloat.

**Federation & security** *(current behavior: see ## Features)*: Full mTLS + ed25519 federation with formal compliance certifications is out of scope for v1 (it's a multi-quarter, legal-sign-off effort, not just code — faking a compliance toggle is exactly the theater this project exists to avoid). Security from Phase 0 onward: `security/sandbox.py` (resource limits + working-directory jail + allowlist, never blocklist, for bash-like tools).

**CLI/UX**: a golden path of five commands — `swarmkit init`, `swarmkit daemon start|stop|status`, `swarmkit run "<goal>"` (auto-spins coordinator+agents, streams, exits), `swarmkit status`, `swarmkit memory query "<text>"` — with advanced flags (`swarm init --topology mesh`, `agent spawn|list|add`, `peer add|list|remove`, `mcp serve|connect`, `config show|set`) discoverable via `--help` but never required on first run.

## Module layout (repo root)

```
swarmkit/
  Cargo.toml                 # workspace root
  pyproject.toml             # MIT license, Python 3.11+ floor, maturin build backend
  README.md
  LICENSE                     # MIT
  crates/
    swarmkit-core/           # lib crate: worker pool, task queue, sandboxed subprocess exec, vector store
      src/{worker_pool.rs, taskqueue.rs, sandbox.rs, vectors.rs}
    swarmkit-py/              # PyO3 binding crate -> builds swarmkit._native
      src/lib.rs
  src/swarmkit/
    cli/             main.py, daemon_client.py
    daemon/          server.py, supervisor.py, agent_tasks.py       # agent_tasks.py: asyncio registry for LLM-driven agent runs (not Rust — network I/O, not compute)
    core/            config.py, providers/{base,anthropic_provider,registry}.py, tokens.py, cache.py, logging.py
    agents/          catalog.py, base.py, loader.py, definitions/{coder,reviewer,tester,docs,architect}.yaml
    swarm/           topology.py, coordinator.py, consensus.py    # worker_pool/taskqueue logic lives in Rust; this is the Python-side LLM decomposition + thin wrapper
    memory/          store.py, embeddings.py, rag.py               # vectors.py is a thin wrapper calling swarmkit._native
    mcp_server/      server.py, client_tools.py                    # no dedicated Rust hot-path module — see MCP integration scope note above
    federation/       identity.py, transport.py                    # ed25519 identity, explicit peer registry, signed HTTP RPC
    security/         secrets.py, audit.py                          # sandbox enforcement lives in Rust; audit log is an append-only SQLite file (engine-enforced)
    docs/             generate.py                                   # emits a lean AGENTS.md/CLAUDE.md, not 60% boilerplate (`swarmkit docs generate`)
  tests/{unit,integration,benchmarks}/
  scripts/{demo_single_agent.py, demo_swarm.py}
```

## Build order

- **Phase 0** — repo scaffold, Rust/Python build wiring, provider abstraction, single real agent. **Follow-up in this phase**: inspect `vtghub/mcp-native-core`'s real MCP tool schemas and confirm the client-consumption plan in the MCP section still fits. Done when: `swarmkit run "..."` makes a real Anthropic call + real subprocess tool execution (spawned via the Rust `sandbox` module through the PyO3 binding), with a logged `request_id` and a cross-checked OS PID.
- **Phase 1** — worker pool + task queue (Rust) + daemon + CLI wiring. Done when: daemon-mediated `run` dispatches through the Rust worker pool, `status` shows real task/PID info sourced from Rust, and a concurrency test shows wall-clock ≈ max(latency), not sum.
- **Phase 2** — memory/RAG, vector index in Rust. Done when: store-then-retrieve works across process restarts and the bytes/entry benchmark (Rust-side compact format) passes at least 10x smaller than Ruflo's reported ratio.
- **Phase 3** — swarm coordination + multi-agent tasks. Done when: a goal is decomposed into ≥2 concurrent subtasks, quorum-verified, dispatched through the Rust worker pool, and a token-budget test proves lazy agent loading.
- **Phase 4** (done) — MCP server exposing real tools; hot-path execution already ran through existing Rust modules end to end, so no new one was added (see the MCP integration scope note above). Done when: an external MCP client can trigger a real daemon-scheduled task (verified twice — direct tool-function call and full MCP wire protocol via `client_tools.connect_stdio`); swarmkit consumes both a generic external MCP server (a test fixture) and the real, inspected `vtghub/mcp-native-core` binary's `fast_search`/`parse_structure` tools.
- **Phase 5** (done) — security hardening + minimal federation. `security/secrets.py` (regex-based redaction of API keys/tokens/private-key blocks, applied before anything is written to the audit log), `security/audit.py` (append-only SQLite log — `BEFORE UPDATE`/`BEFORE DELETE` triggers `RAISE(ABORT, ...)` at the engine level, not just by API convention; both swarmkitd and CLI-hosted runs open the same file directly, no RPC needed), `federation/identity.py` (ed25519 keypair generated and persisted on first run, survives daemon restart; `PeerRegistry` is explicit JSON, no auto-discovery/trust-on-first-use), `federation/transport.py` (Starlette + uvicorn HTTP listener, every request signed over its canonical JSON payload and verified against the sender's registered key before anything reaches the worker pool). Done when: two local daemons complete a signed cross-daemon task (verified against two real `swarmkitd` OS processes — `tests/integration/test_federation_lifecycle.py` — not just an in-process simulation), and a sandbox test (Rust-enforced) blocks a disallowed command pre-execution (`sandbox::tests::non_allowlisted_command_is_rejected_before_spawn`, already in place since Phase 0; also re-verified end-to-end over the federation transport itself).

**Explicitly out of scope for v1**: full Raft/Byzantine/Gossip consensus, full mTLS + formal compliance certifications, a 106-agent catalog, entity-graph/trajectory-learning RAG, web UIs (CLI + MCP first), and Rust-native embeddings inference (stays Python/`sentence-transformers` until there's a measured reason to move it).

## Verification

Every phase's "done" criterion above is a runnable check, not a vibe:
- Real-provider proof: assert response `request_id` format and non-zero `usage`; VCR-style cassettes for cheap CI runs, periodic live canaries for real API confirmation.
- Real-subprocess proof: cross-check spawned PIDs via `psutil` from the Python side against what the Rust worker pool reports — both must agree.
- Rust/Python boundary proof: unit tests in `crates/swarmkit-core` run under `cargo test` independent of Python; PyO3 binding tests in `tests/unit` call the compiled `swarmkit._native` module directly to confirm the boundary itself isn't the theater (i.e., Python isn't silently falling back to a pure-Python shim when the native module fails to import).
- Context-footprint proof: `count_tokens` assertion that the agent catalog metadata stays under a few thousand tokens regardless of catalog size.
- Memory-size proof: benchmark test tracking bytes-per-entry over time for the Rust-backed vector store, run in CI.
- Concurrency proof: wall-clock timing test for N parallel agent tasks dispatched through the Rust worker pool.
- Security proof: sandbox fuzz test (against the Rust sandbox module) asserting the path-jail/allowlist blocks disallowed commands.
- MCP proof: a scripted client calls every exposed tool — including whatever `vtghub/mcp-native-core` provides once inspected — and asserts a real side effect (not just a 200 response).

Each phase ends with `scripts/demo_single_agent.py` or `scripts/demo_swarm.py` runnable end-to-end against the real Anthropic API as a human-visible smoke test, in addition to the automated tests above.
