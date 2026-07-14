from __future__ import annotations

import asyncio
import json
import os

import click

from swarmkit.agents.base import Agent, AgentConfig
from swarmkit.cli import daemon_client
from swarmkit.cli.daemon_client import DaemonUnavailable
from swarmkit.daemon import supervisor
from swarmkit.daemon.server import DEFAULT_CONCURRENCY, pid_path, socket_path

DEFAULT_MODEL = "claude-sonnet-5"


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

    async def daemon_executor(command: list[str]) -> dict:
        status = await daemon_client.run_command(
            command,
            jail_root=abs_workdir,
            workdir=abs_workdir,
            allowed_executables=list(allowed),
        )
        if status["status"] == "failed":
            raise RuntimeError(status.get("error", "sandboxed command failed"))
        return status["result"]

    agent = Agent(config, executor=daemon_executor)

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


if __name__ == "__main__":
    cli()
