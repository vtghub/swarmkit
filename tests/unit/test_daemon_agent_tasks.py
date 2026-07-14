"""AgentTaskRegistry: status transitions (queued -> running -> completed /
failed), without a live LLM — Agent.run is monkeypatched to a fast fake so
this stays a fast unit test. The real end-to-end path (a genuine Anthropic
call driving a real agent inside swarmkitd) is exercised by the live-canary
integration test in tests/integration/test_mcp_server_live.py.
"""

from __future__ import annotations

import asyncio

from swarmkit.agents.base import Agent, AgentRunResult
from swarmkit.agents.catalog import AgentCatalog
from swarmkit.daemon.agent_tasks import AgentTaskRegistry


async def _noop_executor(command: list[str]) -> dict:
    return {"pid": 1, "exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "duration_ms": 0}


async def _wait_for(registry: AgentTaskRegistry, task_id: str, terminal: set[str]) -> None:
    for _ in range(100):
        status = registry.status(task_id)
        if status.status in terminal:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"task {task_id} never reached a terminal state")


async def test_submit_transitions_to_completed(monkeypatch, tmp_path):
    async def fake_run(self, goal, **kwargs):
        return AgentRunResult(request_id="req_fake", text=f"did: {goal}", input_tokens=1, output_tokens=2)

    monkeypatch.setattr(Agent, "run", fake_run)

    registry = AgentTaskRegistry(AgentCatalog())
    task_id = registry.submit(
        agent_name="coder",
        goal="do the thing",
        jail_root=str(tmp_path),
        workdir=str(tmp_path),
        allowed_executables=["echo"],
        executor=_noop_executor,
    )
    assert registry.status(task_id).status in ("queued", "running")

    await _wait_for(registry, task_id, {"completed", "failed"})

    status = registry.status(task_id)
    assert status.status == "completed"
    assert status.result["text"] == "did: do the thing"
    assert status.result["request_id"] == "req_fake"


async def test_a_failing_agent_run_is_reported_as_failed(monkeypatch, tmp_path):
    async def fake_run(self, goal, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(Agent, "run", fake_run)

    registry = AgentTaskRegistry(AgentCatalog())
    task_id = registry.submit(
        agent_name="coder",
        goal="do the thing",
        jail_root=str(tmp_path),
        workdir=str(tmp_path),
        allowed_executables=["echo"],
        executor=_noop_executor,
    )

    await _wait_for(registry, task_id, {"completed", "failed"})

    status = registry.status(task_id)
    assert status.status == "failed"
    assert "boom" in status.error


def test_unknown_task_returns_none():
    registry = AgentTaskRegistry(AgentCatalog())
    assert registry.status("nope") is None


async def test_list_returns_all_submitted_tasks(monkeypatch, tmp_path):
    async def fake_run(self, goal, **kwargs):
        return AgentRunResult(request_id=None, text="", input_tokens=0, output_tokens=0)

    monkeypatch.setattr(Agent, "run", fake_run)
    registry = AgentTaskRegistry(AgentCatalog())

    ids = [
        registry.submit(
            agent_name="coder",
            goal=f"g{i}",
            jail_root=str(tmp_path),
            workdir=str(tmp_path),
            allowed_executables=[],
            executor=_noop_executor,
        )
        for i in range(3)
    ]
    for task_id in ids:
        await _wait_for(registry, task_id, {"completed", "failed"})

    listed_ids = {tid for tid, _ in registry.list()}
    assert set(ids) <= listed_ids
