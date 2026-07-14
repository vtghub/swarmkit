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
from swarmkit.daemon.server import DEFAULT_CONCURRENCY, pid_path, socket_path
from swarmkit.mcp_server import client_tools
from swarmkit.swarm.coordinator import Coordinator
from swarmkit.swarm.topology import Topology

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
def daemon_start(concurrency: int) -> None:
    """Start swarmkitd if it isn't already running."""
    pid = supervisor.start(concurrency=concurrency)
    click.echo(f"swarmkitd running (pid {pid}, socket {socket_path()})")


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
    concurrently, through swarmkitd's Rust worker pool."""
    abs_workdir = os.path.abspath(workdir)
    supervisor.start(concurrency=concurrency)

    catalog = AgentCatalog()
    coordinator = Coordinator(
        catalog,
        coordinator_model=coordinator_model,
        topology=Topology(topology),
        executor=_daemon_executor(abs_workdir, abs_workdir, list(allowed)),
    )

    async def _run() -> None:
        result = await coordinator.run(
            goal,
            jail_root=abs_workdir,
            workdir=abs_workdir,
            allowed_executables=list(allowed),
        )
        click.echo(f"decomposed into {len(result.subtasks)} subtask(s) ({topology} topology)\n")
        for r in result.results:
            status_label = "ok" if r.success else "FAILED quorum check"
            click.echo(f"--- [{r.subtask.id}] {r.subtask.agent}: {status_label} ---")
            click.echo(f"goal: {r.subtask.goal}")
            click.echo(r.run.text)
            if r.quorum is not None:
                click.echo(f"quorum votes: {r.quorum.votes}")
            click.echo("")
        click.echo(f"overall: {'success' if result.success else 'FAILED'}")

    asyncio.run(_run())


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


if __name__ == "__main__":
    cli()
