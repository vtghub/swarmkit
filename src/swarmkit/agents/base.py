"""A single real agent: one Anthropic conversation whose tool calls execute as
actual sandboxed OS subprocesses via the Rust native module — not a JSON record
pretending a subprocess ran.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from anthropic import beta_async_tool

from swarmkit import _native
from swarmkit.core.providers.anthropic_provider import AnthropicProvider

Executor = Callable[[list[str]], Awaitable[dict[str, Any]]]


def local_executor(
    *,
    jail_root: str,
    workdir: str,
    allowed_executables: list[str],
    timeout_secs: float = 30.0,
) -> Executor:
    """The default in-process executor: calls the Rust sandbox directly, no
    daemon required. Also reusable by anything else that needs to run a
    single sandboxed command the same way an Agent's tool call would (e.g.
    the swarm coordinator's quorum verification step)."""

    async def _execute(command: list[str]) -> dict[str, Any]:
        return await _native.run_sandboxed(
            cmd=command,
            jail_root=jail_root,
            workdir=workdir,
            allowed_executables=allowed_executables,
            timeout_secs=timeout_secs,
        )

    return _execute


@dataclass
class AgentConfig:
    name: str
    model: str
    system_prompt: str
    effort: str = "high"


@dataclass
class AgentRunResult:
    request_id: str | None
    text: str
    input_tokens: int
    output_tokens: int
    sandbox_calls: list[dict[str, Any]] = field(default_factory=list)


class Agent:
    """Runs one goal to completion via the Anthropic tool runner, with a single
    `run_command` tool backed by the Rust sandbox (real PID, real jail, real
    timeout — see crates/swarmkit-core/src/sandbox.rs).

    By default the tool calls `_native.run_sandboxed` directly, in-process
    (Phase 0 behavior — works standalone, no daemon required). Pass `executor`
    to route tool calls elsewhere instead — e.g. through swarmkitd's worker
    pool, for real daemon-mediated execution (see cli/main.py)."""

    def __init__(
        self,
        config: AgentConfig,
        provider: AnthropicProvider | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.config = config
        self.provider = provider or AnthropicProvider()
        self._executor = executor

    async def run(
        self,
        goal: str,
        *,
        jail_root: str,
        workdir: str,
        allowed_executables: list[str],
        timeout_secs: float = 30.0,
        extra_tools: list[Any] | None = None,
        context_hints: list[str] | None = None,
    ) -> AgentRunResult:
        """`extra_tools` are additional tool_runner-compatible tools appended
        alongside run_command — e.g. tools from an external MCP server via
        mcp_server.client_tools.connect_stdio(), so an agent can use
        third-party capabilities (search, parsing, ...) in the same loop.

        `context_hints` are plain-text lines prepended to the goal as
        concrete precedent before it reaches the model — e.g. lessons from
        past attempts at similar goals (memory/trajectories.py). Agent stays
        unaware of where hints come from; it just sees them as part of its
        own input, same as any other part of the prompt."""
        sandbox_calls: list[dict[str, Any]] = []
        executor = self._executor or local_executor(
            jail_root=jail_root,
            workdir=workdir,
            allowed_executables=allowed_executables,
            timeout_secs=timeout_secs,
        )

        @beta_async_tool
        async def run_command(command: list[str]) -> str:
            """Run an allowlisted shell command in the agent's sandboxed working directory.

            Args:
                command: The argv vector to execute, e.g. ["echo", "hello"].
            """
            result = await executor(command)
            sandbox_calls.append({"command": command, **result})
            return json.dumps(result)

        content = goal
        if context_hints:
            hints_block = "\n".join(f"- {hint}" for hint in context_hints)
            content = f"Relevant past experience:\n{hints_block}\n\nYour goal: {goal}"

        runner = self.provider.client.beta.messages.tool_runner(
            model=self.config.model,
            max_tokens=8192,
            system=self.config.system_prompt,
            thinking={"type": "adaptive"},
            output_config={"effort": self.config.effort},
            tools=[run_command, *(extra_tools or [])],
            messages=[{"role": "user", "content": content}],
        )

        last = None
        async for message in runner:
            last = message

        text = next((b.text for b in last.content if b.type == "text"), "") if last else ""
        return AgentRunResult(
            request_id=last._request_id if last else None,
            text=text,
            input_tokens=last.usage.input_tokens if last else 0,
            output_tokens=last.usage.output_tokens if last else 0,
            sandbox_calls=sandbox_calls,
        )
