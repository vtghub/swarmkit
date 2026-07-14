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
    ) -> AgentRunResult:
        sandbox_calls: list[dict[str, Any]] = []

        async def default_executor(command: list[str]) -> dict[str, Any]:
            return await _native.run_sandboxed(
                cmd=command,
                jail_root=jail_root,
                workdir=workdir,
                allowed_executables=allowed_executables,
                timeout_secs=timeout_secs,
            )

        executor = self._executor or default_executor

        @beta_async_tool
        async def run_command(command: list[str]) -> str:
            """Run an allowlisted shell command in the agent's sandboxed working directory.

            Args:
                command: The argv vector to execute, e.g. ["echo", "hello"].
            """
            result = await executor(command)
            sandbox_calls.append(result)
            return json.dumps(result)

        runner = self.provider.client.beta.messages.tool_runner(
            model=self.config.model,
            max_tokens=8192,
            system=self.config.system_prompt,
            thinking={"type": "adaptive"},
            output_config={"effort": self.config.effort},
            tools=[run_command],
            messages=[{"role": "user", "content": goal}],
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
