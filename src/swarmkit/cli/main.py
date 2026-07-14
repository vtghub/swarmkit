from __future__ import annotations

import asyncio
import json
import os

import click

from swarmkit.agents.base import Agent, AgentConfig

DEFAULT_MODEL = "claude-sonnet-5"


@click.group()
def cli() -> None:
    """swarmkit — a real, non-theater agent orchestrator (Phase 0: single agent)."""


@cli.command()
def init() -> None:
    """Check that ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN is set."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        click.echo("No ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN set. Set one before running `swarmkit run`.")
        raise SystemExit(1)
    click.echo('swarmkit is ready. Run: swarmkit run "<goal>"')


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
def run(goal: str, model: str, workdir: str, allowed: tuple[str, ...]) -> None:
    """Run GOAL to completion with a single real agent."""
    abs_workdir = os.path.abspath(workdir)
    config = AgentConfig(
        name="cli-agent",
        model=model,
        system_prompt=(
            "You are a focused coding/ops assistant. Use the run_command tool to "
            "execute shell commands when needed. Be concise."
        ),
    )
    agent = Agent(config)

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
            f"sandbox_calls: {json.dumps(result.sandbox_calls, indent=2)}"
        )

    asyncio.run(_run())


if __name__ == "__main__":
    cli()
