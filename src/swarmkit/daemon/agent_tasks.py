"""Agent-run task registry for swarmkitd.

Unlike sandbox command tasks (dispatched to the Rust worker pool — real OS
subprocesses, see crates/swarmkit-core/src/{taskqueue,worker_pool}.rs), an
agent task drives the Anthropic tool-runner loop: I/O-bound network traffic,
not compute Rust would speed up (the same reasoning that keeps the provider
layer in Python — see docs/PLAN.md). This registry gives spawn_agent the same
submit-now, poll-status-later shape as sandbox tasks, implemented directly in
asyncio since that's what's actually running underneath. The agent's own
tool calls still go through a Rust-worker-pool-backed executor, so the parts
that benefit from Rust still get it.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from swarmkit.agents.base import Agent, AgentConfig, Executor
from swarmkit.agents.catalog import AgentCatalog
from swarmkit.security.audit import AuditLog


@dataclass
class AgentTaskStatus:
    status: str  # "queued" | "running" | "completed" | "failed"
    result: dict[str, Any] | None = None
    error: str | None = None


class AgentTaskRegistry:
    def __init__(self, catalog: AgentCatalog, audit: AuditLog | None = None) -> None:
        self._catalog = catalog
        self._audit = audit
        self._statuses: dict[str, AgentTaskStatus] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def submit(
        self,
        *,
        agent_name: str,
        goal: str,
        jail_root: str,
        workdir: str,
        allowed_executables: list[str],
        executor: Executor,
    ) -> str:
        task_id = str(uuid.uuid4())
        self._statuses[task_id] = AgentTaskStatus(status="queued")
        self._tasks[task_id] = asyncio.create_task(
            self._run(task_id, agent_name, goal, jail_root, workdir, allowed_executables, executor)
        )
        return task_id

    async def _run(
        self,
        task_id: str,
        agent_name: str,
        goal: str,
        jail_root: str,
        workdir: str,
        allowed_executables: list[str],
        executor: Executor,
    ) -> None:
        self._statuses[task_id] = AgentTaskStatus(status="running")
        try:
            definition = self._catalog.load(agent_name)
            agent = Agent(
                AgentConfig(
                    name=definition.name,
                    model=definition.default_model,
                    system_prompt=definition.system_prompt,
                    effort=definition.default_effort,
                ),
                executor=executor,
            )
            result = await agent.run(
                goal,
                jail_root=jail_root,
                workdir=workdir,
                allowed_executables=allowed_executables,
            )
            self._statuses[task_id] = AgentTaskStatus(
                status="completed",
                result={
                    "text": result.text,
                    "request_id": result.request_id,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "sandbox_calls": result.sandbox_calls,
                },
            )
            if self._audit is not None:
                await self._audit.record_agent_run(
                    model=definition.default_model,
                    request_id=result.request_id,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    sandbox_calls=result.sandbox_calls,
                )
        except Exception as e:  # noqa: BLE001 - surface any failure via status, don't crash the daemon
            self._statuses[task_id] = AgentTaskStatus(status="failed", error=str(e))

    def status(self, task_id: str) -> AgentTaskStatus | None:
        return self._statuses.get(task_id)

    def list(self) -> list[tuple[str, AgentTaskStatus]]:
        return list(self._statuses.items())
