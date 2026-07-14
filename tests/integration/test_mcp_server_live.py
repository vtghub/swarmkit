"""Live canary: an external MCP client triggers a real daemon-scheduled task
— the Phase 4 'done' criterion, exercised twice: once calling swarmkit's MCP
tool functions directly, and once over the actual MCP wire protocol via
client_tools.connect_stdio, so the whole stack (client -> stdio -> FastMCP ->
daemon socket -> Rust worker pool -> real Anthropic call) is proven, not
just its pieces in isolation. Skipped without a real credential.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile

import pytest

from swarmkit.daemon import supervisor
from swarmkit.mcp_server import client_tools
from swarmkit.mcp_server.server import get_task_status, spawn_agent

pytestmark = pytest.mark.skipif(
    not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
    reason="requires a real Anthropic credential",
)


@pytest.fixture
def isolated_runtime_dir(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="swarmkit-test-")
    monkeypatch.setenv("SWARMKIT_RUNTIME_DIR", tmp)
    yield tmp
    supervisor.stop()
    shutil.rmtree(tmp, ignore_errors=True)


async def test_spawn_agent_tool_function_runs_a_real_agent_via_the_daemon(
    isolated_runtime_dir, tmp_path
):
    supervisor.start(concurrency=2)

    response = await spawn_agent(
        agent_name="tester",
        goal="Run `echo mcp-spawn-proof` with the run_command tool and report its output.",
        jail_root=str(tmp_path),
        workdir=str(tmp_path),
        allowed_executables=["echo"],
    )
    task_id = response["task_id"]

    status = None
    for _ in range(200):
        status = await get_task_status(task_id)
        if status["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.1)

    assert status is not None
    assert status["status"] == "completed", status
    assert status["result"]["sandbox_calls"], "agent never used the sandboxed run_command tool"


async def test_external_mcp_client_triggers_a_real_daemon_scheduled_task_over_the_wire(
    isolated_runtime_dir, tmp_path
):
    supervisor.start(concurrency=2)

    # swarmkit's own server is a trusted local subprocess: fully inherit the
    # environment so it sees both ANTHROPIC_API_KEY and the test's
    # SWARMKIT_RUNTIME_DIR override (mcp's stdio client otherwise only passes
    # a curated safe subset of env vars to the child by default).
    async with client_tools.connect_stdio(
        sys.executable, ["-m", "swarmkit.mcp_server.server"], env=dict(os.environ)
    ) as tools:
        spawn_tool = next(t for t in tools if t.name == "spawn_agent")
        status_tool = next(t for t in tools if t.name == "get_task_status")

        spawn_result = await spawn_tool.call(
            {
                "agent_name": "tester",
                "goal": "Run `echo mcp-wire-proof` with the run_command tool and report its output.",
                "jail_root": str(tmp_path),
                "workdir": str(tmp_path),
                "allowed_executables": ["echo"],
            }
        )
        task_id = json.loads(spawn_result[0]["text"])["task_id"]

        status = None
        for _ in range(200):
            status_result = await status_tool.call({"task_id": task_id})
            status = json.loads(status_result[0]["text"])
            if status["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

    assert status is not None
    assert status["status"] == "completed", status
    assert status["result"]["sandbox_calls"], "agent never used the sandboxed run_command tool"
