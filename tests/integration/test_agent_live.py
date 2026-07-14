"""Live canary: a real Anthropic call whose tool use runs as a real sandboxed
subprocess, end to end. Skipped without a real credential — this is the test
that proves Phase 0's "done" criterion, not a mock."""

from __future__ import annotations

import os

import pytest

from swarmkit.agents.base import Agent, AgentConfig

pytestmark = pytest.mark.skipif(
    not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
    reason="requires a real Anthropic credential",
)


async def test_single_agent_real_call_and_real_subprocess(tmp_path):
    config = AgentConfig(
        name="test-agent",
        model="claude-haiku-4-5",
        system_prompt="Use the run_command tool to run exactly one command, then stop.",
    )
    agent = Agent(config)

    result = await agent.run(
        "Run `echo swarmkit-live-test` using the run_command tool, then tell me its output.",
        jail_root=str(tmp_path),
        workdir=str(tmp_path),
        allowed_executables=["echo"],
    )

    assert (result.request_id or "").startswith("req_")
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.sandbox_calls, "agent never invoked the sandboxed run_command tool"
    for call in result.sandbox_calls:
        assert call["pid"] > 0
        assert call["exit_code"] == 0
        assert not call["timed_out"]
