"""Generates a lean AGENTS.md/CLAUDE.md for a swarmkit-using project.

This is the direct fix for Ruflo's documented ~60%-boilerplate agent doc: the
one part of this file that scales with catalog size — the agent list — is
rendered from `AgentCatalog.render_summary()`, the same name+description-only
view the coordinator itself sees. It structurally cannot balloon into 300K
tokens of persona text, because the catalog itself never loads persona text
until an agent is actually spawned. Everything else below is a short,
current description of swarmkit's real (not aspirational) v1 feature
surface: single-agent runs, swarm coordination, memory/RAG, MCP, security,
and federation.
"""

from __future__ import annotations

from pathlib import Path

from swarmkit.agents.catalog import AgentCatalog

_INTRO = """\
# swarmkit

A real, non-theater multi-agent orchestrator. Every capability below is
backed by a runnable test that observes an external side effect (a real
subprocess PID, a real Anthropic `request_id`, a measured byte size, a
measured wall-clock concurrency) — see `docs/PLAN.md` for the verification
approach. If a capability isn't listed here, it doesn't exist yet."""

_GOLDEN_PATH = """\
## Golden path

```
swarmkit init                          # checks ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN
swarmkit daemon start                  # background daemon owning the real Rust worker pool
swarmkit run "<goal>"                  # one agent, tool calls sandboxed via the daemon
swarmkit swarm run "<goal>"            # decompose into concurrent, quorum-verified subtasks
swarmkit status                        # real task/PID info, sourced from Rust
swarmkit audit                         # every tool call + provider request, append-only
swarmkit daemon stop
```"""

_SWARM = """\
## Swarm coordination

`swarmkit swarm run "<goal>" --topology star|mesh` decomposes a goal into
subtasks via the Anthropic API's structured output, then dispatches them
concurrently through swarmkitd's Rust worker pool. `star` has no concurrency
cap; `mesh` caps fan-out at 8 concurrent agents (a real enforced limit, not
a marketing number). Subtasks with a `verify_command` are quorum-verified:
re-run across 3 independent replicas, accepted only on majority agreement —
a real reliability mechanism, not an unverifiable Raft/Byzantine/Gossip
claim. Full Raft/Byzantine/Gossip consensus is explicitly out of scope for
v1."""

_MEMORY = """\
## Memory / RAG

SQLite (`memory.db`) + FTS5 for keyword search, a compact Rust-backed
vector store (`vectors.bin`, fixed-width binary format, lazy `instant-distance`
HNSW) for semantic search. Retrieval combines both via Reciprocal Rank
Fusion, then re-ranks with MMR for diversity. Measured at ~1KB/entry on
disk — orders of magnitude smaller than Ruflo's reported ~5MB/entry.
Embeddings are pluggable: a dependency-free `HashingEmbedder` by default, or
`SentenceTransformerEmbedder` (`pip install 'swarmkit[embeddings]'`) for
real semantic quality."""

_MCP = """\
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
a real third-party MCP server binary, not just a protocol mock."""

_SECURITY_FEDERATION = """\
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
and formal compliance certifications are explicitly out of scope for v1."""

_FOOTER = """\
See `docs/PLAN.md` for the full build order, what's deferred, and how each
claim above is verified."""

# The capability descriptions below are the single source of truth for what
# each part of swarmkit does — used to build AGENTS.md/CLAUDE.md (generate(),
# below) and also synced verbatim into this repo's own README.md and
# docs/PLAN.md (sync_repo_docs(), below) so a capability's description is
# written once, not hand-duplicated across three files that drift apart.
FEATURE_SECTIONS: dict[str, str] = {
    "golden_path": _GOLDEN_PATH,
    "swarm": _SWARM,
    "memory": _MEMORY,
    "mcp": _MCP,
    "security_federation": _SECURITY_FEDERATION,
}

FEATURES_MARKER_START = "<!-- swarmkit:generated-features:start -->"
FEATURES_MARKER_END = "<!-- swarmkit:generated-features:end -->"


def feature_markdown() -> str:
    """The shared capability descriptions, without the AGENTS.md-specific
    intro/footer — this is what gets embedded into README.md/docs/PLAN.md
    between the generated-features markers."""
    return "\n\n".join(FEATURE_SECTIONS.values())


def _replace_marked_block(text: str, replacement: str) -> str:
    start_idx = text.index(FEATURES_MARKER_START) + len(FEATURES_MARKER_START)
    end_idx = text.index(FEATURES_MARKER_END)
    return f"{text[:start_idx]}\n\n{replacement}\n\n{text[end_idx:]}"


def sync_repo_docs(repo_root: str | Path = ".") -> list[Path]:
    """Replace the generated-features block in *this repo's own*
    README.md and docs/PLAN.md with the current feature_markdown() output.
    Run this (via scripts/sync_docs.py) after changing any FEATURE_SECTIONS
    entry, instead of hand-editing the same capability description into
    both files. Returns the paths that were actually changed."""
    root = Path(repo_root)
    replacement = feature_markdown()
    changed: list[Path] = []
    for relpath in ("README.md", "docs/PLAN.md"):
        path = root / relpath
        original = path.read_text()
        updated = _replace_marked_block(original, replacement)
        if updated != original:
            path.write_text(updated)
            changed.append(path)
    return changed


def _agent_catalog_section(catalog: AgentCatalog) -> str:
    summary = catalog.render_summary()
    return (
        "## Agent catalog\n\n"
        f"{summary}\n\n"
        "Only name + description ever reach an LLM's context (a few hundred "
        "tokens total, regardless of catalog size) — full persona and tool "
        "schema load only at actual spawn time. Add an agent by dropping a "
        "YAML file into `agents/definitions/` or "
        "`~/.config/swarmkit/agents/`."
    )


def generate(catalog: AgentCatalog | None = None) -> str:
    """Render the full AGENTS.md/CLAUDE.md content (identical for both — the
    same lean doc works for either assistant)."""
    catalog = catalog or AgentCatalog()
    sections = [
        _INTRO,
        _GOLDEN_PATH,
        _agent_catalog_section(catalog),
        _SWARM,
        _MEMORY,
        _MCP,
        _SECURITY_FEDERATION,
        _FOOTER,
    ]
    return "\n\n".join(sections) + "\n"


def write(target_dir: str | Path = ".", catalog: AgentCatalog | None = None) -> tuple[Path, Path]:
    """Write AGENTS.md and CLAUDE.md into `target_dir`. Returns their paths."""
    target = Path(target_dir)
    content = generate(catalog)
    agents_path = target / "AGENTS.md"
    claude_path = target / "CLAUDE.md"
    agents_path.write_text(content)
    claude_path.write_text(content)
    return agents_path, claude_path


def main() -> None:
    import sys

    target_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    agents_path, claude_path = write(target_dir)
    print(f"wrote {agents_path}")
    print(f"wrote {claude_path}")


if __name__ == "__main__":
    main()
