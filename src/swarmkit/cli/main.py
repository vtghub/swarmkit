from __future__ import annotations

import asyncio
import json
import os

import click

from swarmkit.agents.base import Agent, AgentConfig, Executor
from swarmkit.agents.catalog import AgentCatalog
from swarmkit.cli import daemon_client
from swarmkit.cli.daemon_client import DaemonUnavailable
from swarmkit.daemon import supervisor
from swarmkit.daemon.server import (
    DEFAULT_CONCURRENCY,
    audit_db_path,
    identity_key_path,
    peers_path,
    pid_path,
    socket_path,
    trajectories_db_path,
    trajectories_vectors_path,
)
from swarmkit.federation.identity import PeerRegistry, load_or_create_identity
from swarmkit.mcp_server import client_tools
from swarmkit.memory.embeddings import HashingEmbedder
from swarmkit.memory.trajectories import TrajectoryStore
from swarmkit.memory.vectors import open_vector_store
from swarmkit.security.audit import AuditLog
from swarmkit.swarm.coordinator import Coordinator
from swarmkit.swarm.topology import Topology

TRAJECTORY_EMBEDDING_DIM = 384

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_COORDINATOR_MODEL = "claude-opus-4-8"


def _daemon_executor(jail_root: str, workdir: str, allowed: list[str]) -> Executor:
    """A tool-call executor that submits to swarmkitd's worker pool and polls
    until done — the daemon-mediated replacement for calling the sandbox
    in-process, shared by `run` and `swarm run`."""

    async def _execute(command: list[str]) -> dict:
        status = await daemon_client.run_command(
            command,
            jail_root=jail_root,
            workdir=workdir,
            allowed_executables=allowed,
        )
        if status["status"] == "failed":
            raise RuntimeError(status.get("error", "sandboxed command failed"))
        return status["result"]

    return _execute


@click.group()
def cli() -> None:
    """swarmkit — a real, non-theater agent orchestrator."""


@cli.command()
def init() -> None:
    """Check that ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN is set."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        click.echo("No ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN set. Set one before running `swarmkit run`.")
        raise SystemExit(1)
    click.echo('swarmkit is ready. Run: swarmkit run "<goal>"')


@cli.group()
def daemon() -> None:
    """Manage the swarmkitd background daemon (owns the real worker pool)."""


@daemon.command("start")
@click.option("--concurrency", default=DEFAULT_CONCURRENCY, show_default=True)
@click.option(
    "--federation-port",
    type=int,
    default=None,
    help="If set, also listen for signed cross-daemon task requests on this port.",
)
@click.option("--federation-host", default="127.0.0.1", show_default=True)
def daemon_start(concurrency: int, federation_port: int | None, federation_host: str) -> None:
    """Start swarmkitd if it isn't already running."""
    pid = supervisor.start(
        concurrency=concurrency, federation_host=federation_host, federation_port=federation_port
    )
    click.echo(f"swarmkitd running (pid {pid}, socket {socket_path()})")
    if federation_port is not None:
        click.echo(f"federation listener on {federation_host}:{federation_port}")


@daemon.command("stop")
def daemon_stop() -> None:
    """Stop swarmkitd."""
    if supervisor.stop():
        click.echo("swarmkitd stopped")
    else:
        click.echo("swarmkitd was not running")


@daemon.command("status")
def daemon_status() -> None:
    """Show whether swarmkitd is running, with a real ping round-trip."""
    pid = supervisor.is_running()
    if pid is None:
        click.echo("swarmkitd is not running")
        raise SystemExit(1)
    response = asyncio.run(daemon_client.ping())
    click.echo(f"swarmkitd running (pid {response.get('pid', pid)}, socket {socket_path()})")


@cli.command()
def status() -> None:
    """List tasks currently known to swarmkitd's worker pool (real task ids and PIDs, sourced from Rust)."""
    try:
        tasks = asyncio.run(daemon_client.list_tasks())
    except DaemonUnavailable as e:
        click.echo(str(e))
        raise SystemExit(1) from e
    if not tasks:
        click.echo("no tasks")
        return
    for task_id, task in tasks:
        click.echo(f"{task_id}: {json.dumps(task)}")


@cli.command("identity")
def identity_show() -> None:
    """Show this daemon's ed25519 public key — give this to other operators
    so they can `swarmkit peer add` you. Generates the identity on first
    call if it doesn't exist yet; no daemon needs to be running."""
    identity = load_or_create_identity(identity_key_path())
    click.echo(identity.public_key_hex)


@cli.group()
def peer() -> None:
    """Manage explicitly-registered federation peers (no auto-discovery:
    a peer exists only because you ran `peer add` with its public key,
    exchanged out-of-band)."""


@peer.command("add")
@click.argument("name")
@click.argument("host")
@click.argument("port", type=int)
@click.argument("public_key_hex")
def peer_add(name: str, host: str, port: int, public_key_hex: str) -> None:
    """Register a peer daemon by name, address, and its public key (as shown
    by that daemon's `swarmkit identity`)."""
    registry = PeerRegistry(peers_path())
    registry.add(name, host, port, public_key_hex)
    click.echo(f"added peer {name!r} ({host}:{port})")


@peer.command("list")
def peer_list() -> None:
    """List registered peers."""
    registry = PeerRegistry(peers_path())
    peers = registry.list()
    if not peers:
        click.echo("no peers registered")
        return
    for p in peers:
        click.echo(f"{p.name}: {p.host}:{p.port} ({p.public_key_hex[:16]}...)")


@peer.command("remove")
@click.argument("name")
def peer_remove(name: str) -> None:
    """Remove a registered peer by name."""
    registry = PeerRegistry(peers_path())
    if registry.remove(name):
        click.echo(f"removed peer {name!r}")
    else:
        click.echo(f"no such peer {name!r}")
        raise SystemExit(1)


@cli.command()
@click.option("--event-type", type=click.Choice(["tool_call", "provider_request"]), default=None)
@click.option("--limit", default=20, show_default=True)
def audit(event_type: str | None, limit: int) -> None:
    """Show recent audit log entries (every sandboxed subprocess execution
    and every Anthropic provider request — see security/audit.py). Reads the
    log file directly; no daemon needs to be running."""

    async def _query() -> list[dict]:
        log = AuditLog(audit_db_path())
        try:
            return await log.query(event_type=event_type, limit=limit)
        finally:
            log.close()

    entries = asyncio.run(_query())
    if not entries:
        click.echo("no audit entries")
        return
    for e in entries:
        click.echo(f"[{e['id']}] {e['event_type']} @ {e['timestamp']:.3f}: {json.dumps(e['details'])}")


@cli.command()
@click.argument("goal")
@click.option("--model", default=DEFAULT_MODEL, show_default=True)
@click.option(
    "--workdir",
    default=".",
    show_default=True,
    help="Sandbox jail root and working directory for this run.",
)
@click.option(
    "--allow",
    "allowed",
    multiple=True,
    default=("echo", "ls", "cat", "pwd"),
    help="Executable names the agent's run_command tool may invoke.",
)
@click.option(
    "--concurrency",
    default=DEFAULT_CONCURRENCY,
    show_default=True,
    help="Worker pool size, used only if swarmkitd needs to be started for this run.",
)
def run(goal: str, model: str, workdir: str, allowed: tuple[str, ...], concurrency: int) -> None:
    """Run GOAL to completion with a single real agent, dispatching its tool
    calls through swarmkitd's Rust worker pool (started automatically if not
    already running)."""
    abs_workdir = os.path.abspath(workdir)
    supervisor.start(concurrency=concurrency)

    config = AgentConfig(
        name="cli-agent",
        model=model,
        system_prompt=(
            "You are a focused coding/ops assistant. Use the run_command tool to "
            "execute shell commands when needed. Be concise."
        ),
    )

    agent = Agent(config, executor=_daemon_executor(abs_workdir, abs_workdir, list(allowed)))

    async def _run() -> None:
        result = await agent.run(
            goal,
            jail_root=abs_workdir,
            workdir=abs_workdir,
            allowed_executables=list(allowed),
        )
        click.echo(result.text)
        click.echo(
            "\n--- run metadata (proof this was real) ---\n"
            f"request_id: {result.request_id}\n"
            f"input_tokens: {result.input_tokens}  output_tokens: {result.output_tokens}\n"
            f"daemon pid: {pid_path().read_text().strip() if pid_path().exists() else 'unknown'}\n"
            f"sandbox_calls: {json.dumps(result.sandbox_calls, indent=2)}"
        )

        audit = AuditLog(audit_db_path())
        try:
            await audit.record_agent_run(
                model=model,
                request_id=result.request_id,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                sandbox_calls=result.sandbox_calls,
            )
        finally:
            audit.close()

    asyncio.run(_run())


@cli.group()
def swarm() -> None:
    """Multi-agent swarm coordination (goal decomposition + concurrent dispatch)."""


@swarm.command("run")
@click.argument("goal")
@click.option("--coordinator-model", default=DEFAULT_COORDINATOR_MODEL, show_default=True)
@click.option(
    "--topology",
    type=click.Choice([t.value for t in Topology]),
    default=Topology.STAR.value,
    show_default=True,
)
@click.option(
    "--workdir",
    default=".",
    show_default=True,
    help="Sandbox jail root and working directory for every subtask.",
)
@click.option(
    "--allow",
    "allowed",
    multiple=True,
    default=("echo", "ls", "cat", "pwd"),
    help="Executable names subtask agents' run_command tool may invoke.",
)
@click.option(
    "--concurrency",
    default=DEFAULT_CONCURRENCY,
    show_default=True,
    help="Worker pool size, used only if swarmkitd needs to be started for this run.",
)
def swarm_run(
    goal: str,
    coordinator_model: str,
    topology: str,
    workdir: str,
    allowed: tuple[str, ...],
    concurrency: int,
) -> None:
    """Decompose GOAL into subtasks and dispatch them to catalog agents
    concurrently, through swarmkitd's Rust worker pool. Verified subtasks
    (those with a verify_command) both draw on and contribute to trajectory
    memory (swarmkit trajectories) — the real success/failure signal quorum
    verification already produces is the only thing that triggers it."""
    abs_workdir = os.path.abspath(workdir)
    supervisor.start(concurrency=concurrency)

    catalog = AgentCatalog()
    trajectory_vectors = open_vector_store(trajectories_vectors_path())
    trajectories = TrajectoryStore(
        trajectories_db_path(), trajectory_vectors, HashingEmbedder(dim=TRAJECTORY_EMBEDDING_DIM)
    )
    coordinator = Coordinator(
        catalog,
        coordinator_model=coordinator_model,
        topology=Topology(topology),
        executor=_daemon_executor(abs_workdir, abs_workdir, list(allowed)),
        trajectories=trajectories,
    )

    async def _run() -> None:
        result = await coordinator.run(
            goal,
            jail_root=abs_workdir,
            workdir=abs_workdir,
            allowed_executables=list(allowed),
        )
        click.echo(f"decomposed into {len(result.subtasks)} subtask(s) ({topology} topology)\n")

        audit = AuditLog(audit_db_path())
        try:
            for r in result.results:
                status_label = "ok" if r.success else "FAILED quorum check"
                click.echo(f"--- [{r.subtask.id}] {r.subtask.agent}: {status_label} ---")
                click.echo(f"goal: {r.subtask.goal}")
                click.echo(r.run.text)
                if r.quorum is not None:
                    click.echo(f"quorum votes: {r.quorum.votes}")
                click.echo("")

                definition = catalog.load(r.subtask.agent)
                await audit.record_agent_run(
                    model=definition.default_model,
                    request_id=r.run.request_id,
                    input_tokens=r.run.input_tokens,
                    output_tokens=r.run.output_tokens,
                    sandbox_calls=r.run.sandbox_calls,
                )
        finally:
            audit.close()

        click.echo(f"overall: {'success' if result.success else 'FAILED'}")

    try:
        asyncio.run(_run())
    finally:
        trajectory_vectors.save(str(trajectories_vectors_path()))
        trajectories.close()


@cli.command()
@click.option("--agent", "agent_name", default=None, help="Filter to trajectories recorded for this agent.")
@click.option("--outcome", type=click.Choice(["success", "failure"]), default=None)
@click.option("--limit", default=20, show_default=True)
def trajectories(agent_name: str | None, outcome: str | None, limit: int) -> None:
    """Show recorded trajectories: past `swarm run` subtasks that had a
    verify_command, the real success/failure signal quorum verification
    produced, and the mechanically-derived lesson — swarmkit's self-learning
    memory (see memory/trajectories.py). Reads the log file directly; no
    daemon needs to be running."""
    db_path = trajectories_db_path()
    if not db_path.exists():
        click.echo("no trajectories recorded yet")
        return

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        query = "SELECT id, agent_name, goal, outcome, lesson, created_at FROM trajectories WHERE 1=1"
        params: list[str | int] = []
        if agent_name:
            query += " AND agent_name = ?"
            params.append(agent_name)
        if outcome:
            query += " AND outcome = ?"
            params.append(outcome)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    if not rows:
        click.echo("no trajectories recorded yet")
        return
    for row in rows:
        _id, agent, goal, out, lesson, created_at = row
        click.echo(f"[{out}] {agent} @ {created_at:.3f}")
        click.echo(f"  goal: {goal}")
        click.echo(f"  lesson: {lesson}")


@cli.group()
def mcp() -> None:
    """MCP integration: serve swarmkit's own tools, or inspect an external server."""


@mcp.command("serve")
def mcp_serve() -> None:
    """Run swarmkit's own MCP server over stdio (spawn_agent, get_task_status,
    list_agents, query_memory) — point an MCP client (e.g. Claude Code) at
    this command."""
    from swarmkit.mcp_server.server import main as serve_main

    serve_main()


@mcp.command("list-tools")
@click.argument("command")
@click.argument("args", nargs=-1)
def mcp_list_tools(command: str, args: tuple[str, ...]) -> None:
    """List the tools an external stdio MCP server exposes, e.g.:

    swarmkit mcp list-tools /path/to/mcp-native-core
    """
    tools = asyncio.run(client_tools.list_tools(command, list(args)))
    for t in tools:
        click.echo(f"{t['name']}: {t['description']}")
        click.echo(f"  input_schema: {json.dumps(t['input_schema'])}")


@cli.group()
def docs() -> None:
    """Generate project documentation."""


@docs.command("generate")
@click.option(
    "--dir",
    "target_dir",
    default=".",
    show_default=True,
    help="Directory to write AGENTS.md and CLAUDE.md into.",
)
def docs_generate(target_dir: str) -> None:
    """Emit a lean AGENTS.md/CLAUDE.md reflecting swarmkit's real feature
    surface — the agent catalog section is rendered from the live catalog,
    so it can never balloon past what's actually installed."""
    from swarmkit.docs.generate import write

    agents_path, claude_path = write(target_dir)
    click.echo(f"wrote {agents_path}")
    click.echo(f"wrote {claude_path}")


if __name__ == "__main__":
    cli()
