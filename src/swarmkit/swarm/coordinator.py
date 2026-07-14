"""Swarm coordinator: decomposes a goal into a subtask DAG via the Anthropic
API's structured output (so parsing never depends on scraping free text),
then dispatches subtasks concurrently — real concurrent tool-runner agents,
gated only by the topology's concurrency cap, not a fake fan-out.

Subtasks that name a `verify_command` are checked with quorum voting
(swarm/consensus.py): the command is re-run independently across replicas
and the subtask is only accepted on majority agreement. That same
quorum-verified outcome is also the only real success/failure signal this
coordinator ever has — so it's the only thing that triggers trajectory
recording (memory/trajectories.py): unverified subtasks are never guessed
at as "success" or "failure". When a TrajectoryStore is configured, a
verified subtask both retrieves relevant past lessons for its agent before
running (folded into the prompt via Agent.run's context_hints) and records
its own outcome afterward — the mechanism that makes a swarm's next run
draw on what an earlier run actually learned.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from swarmkit.agents.base import Agent, AgentConfig, AgentRunResult, Executor, local_executor
from swarmkit.agents.catalog import AgentCatalog
from swarmkit.core.providers.anthropic_provider import AnthropicProvider
from swarmkit.memory.trajectories import TrajectoryStore, format_hint
from swarmkit.swarm.consensus import QuorumResult, quorum_execute
from swarmkit.swarm.topology import Topology, max_concurrent_agents

SUBTASK_SCHEMA = {
    "type": "object",
    "properties": {
        "subtasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Short unique identifier for this subtask"},
                    "agent": {"type": "string", "description": "Name of an agent from the catalog to perform this subtask"},
                    "goal": {"type": "string", "description": "The specific, self-contained instruction for that agent"},
                    "verify": {
                        "type": "boolean",
                        "description": "Whether this subtask's result should be quorum-verified by re-running verify_command",
                    },
                    "verify_command": {
                        "anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}],
                        "description": "An objective check command (e.g. a test runner), required when verify is true; null otherwise",
                    },
                },
                "required": ["id", "agent", "goal", "verify", "verify_command"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["subtasks"],
    "additionalProperties": False,
}


@dataclass
class Subtask:
    id: str
    agent: str
    goal: str
    verify: bool = False
    verify_command: list[str] | None = None


@dataclass
class SubtaskResult:
    subtask: Subtask
    run: AgentRunResult
    quorum: QuorumResult | None = None

    @property
    def success(self) -> bool:
        return self.quorum.accepted if self.quorum is not None else True


@dataclass
class SwarmRunResult:
    goal: str
    subtasks: list[Subtask]
    results: list[SubtaskResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return all(r.success for r in self.results)


async def dispatch_subtasks(
    subtasks: list[Subtask],
    run_one: Callable[[Subtask], Awaitable[SubtaskResult]],
    *,
    concurrency: int,
) -> list[SubtaskResult]:
    """Run `run_one` over every subtask with at most `concurrency` in flight
    at once. A plain reusable concurrency-gated fan-out — see
    tests/unit/test_coordinator_dispatch.py for the proof that this actually
    runs subtasks in parallel, not serially."""
    semaphore = asyncio.Semaphore(max(concurrency, 1))

    async def _guarded(subtask: Subtask) -> SubtaskResult:
        async with semaphore:
            return await run_one(subtask)

    return list(await asyncio.gather(*(_guarded(s) for s in subtasks)))


class Coordinator:
    """Decomposes a goal via the coordinator model, then dispatches subtasks
    to catalog agents concurrently. `executor` controls where subtasks'
    sandboxed tool calls actually run — pass a daemon-backed one (see
    cli/main.py) for real worker-pool-mediated dispatch; omit it for the
    Phase 0 in-process default (useful standalone and in tests)."""

    def __init__(
        self,
        catalog: AgentCatalog,
        *,
        provider: AnthropicProvider | None = None,
        coordinator_model: str = "claude-opus-4-8",
        topology: Topology = Topology.STAR,
        executor: Executor | None = None,
        trajectories: TrajectoryStore | None = None,
    ) -> None:
        self.catalog = catalog
        self.provider = provider or AnthropicProvider()
        self.coordinator_model = coordinator_model
        self.topology = topology
        self._executor = executor
        self.trajectories = trajectories

    async def decompose(self, goal: str) -> list[Subtask]:
        system = (
            "You are a swarm coordinator. Break the user's goal into 1-6 "
            "independent, self-contained subtasks. Assign each to exactly "
            "one agent from this catalog, by name:\n\n"
            f"{self.catalog.render_summary()}\n\n"
            "Set verify=true and give a verify_command only for subtasks "
            "whose result can be objectively checked by re-running a "
            "command (e.g. a test suite or a build). Leave verify=false and "
            "verify_command=null for open-ended tasks like writing prose or "
            "planning."
        )
        response = await self.provider.complete(
            model=self.coordinator_model,
            system=system,
            messages=[{"role": "user", "content": goal}],
            effort="high",
            response_schema=SUBTASK_SCHEMA,
        )
        payload = json.loads(response.text)
        return [Subtask(**item) for item in payload["subtasks"]]

    async def _run_subtask(
        self,
        subtask: Subtask,
        executor: Executor,
        *,
        jail_root: str,
        workdir: str,
        allowed_executables: list[str],
    ) -> SubtaskResult:
        definition = self.catalog.load(subtask.agent)

        context_hints = None
        if self.trajectories is not None:
            past = await self.trajectories.retrieve_relevant(definition.name, subtask.goal)
            context_hints = [format_hint(t) for t in past] or None

        agent = Agent(
            AgentConfig(
                name=definition.name,
                model=definition.default_model,
                system_prompt=definition.system_prompt,
                effort=definition.default_effort,
            ),
            provider=self.provider,
            executor=executor,
        )
        run_result = await agent.run(
            subtask.goal,
            jail_root=jail_root,
            workdir=workdir,
            allowed_executables=allowed_executables,
            context_hints=context_hints,
        )

        quorum: QuorumResult | None = None
        if subtask.verify and subtask.verify_command:
            verify_command = subtask.verify_command

            async def run_once() -> dict[str, Any]:
                return await executor(verify_command)

            quorum = await quorum_execute(run_once)

            if self.trajectories is not None:
                await self.trajectories.record_run(
                    agent_name=definition.name,
                    goal=subtask.goal,
                    outcome="success" if quorum.accepted else "failure",
                    sandbox_calls=run_result.sandbox_calls,
                )

        return SubtaskResult(subtask=subtask, run=run_result, quorum=quorum)

    async def run(
        self,
        goal: str,
        *,
        jail_root: str,
        workdir: str,
        allowed_executables: list[str],
    ) -> SwarmRunResult:
        subtasks = await self.decompose(goal)
        executor = self._executor or local_executor(
            jail_root=jail_root, workdir=workdir, allowed_executables=allowed_executables
        )

        async def run_one(subtask: Subtask) -> SubtaskResult:
            return await self._run_subtask(
                subtask,
                executor,
                jail_root=jail_root,
                workdir=workdir,
                allowed_executables=allowed_executables,
            )

        concurrency = max_concurrent_agents(self.topology, len(subtasks))
        results = await dispatch_subtasks(subtasks, run_one, concurrency=concurrency)
        return SwarmRunResult(goal=goal, subtasks=subtasks, results=results)
