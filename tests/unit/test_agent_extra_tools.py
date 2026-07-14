"""Proves external MCP tools reach an Agent's actual tool_runner call — the
real tools list constructed inside Agent.run — verified without a live LLM
by capturing the kwargs a fake Anthropic client receives."""

from __future__ import annotations

import sys
from pathlib import Path

from swarmkit.agents.base import Agent, AgentConfig
from swarmkit.core.providers.anthropic_provider import AnthropicProvider
from swarmkit.mcp_server.client_tools import connect_stdio

FIXTURE_SERVER = str(Path(__file__).parent.parent / "fixtures" / "echo_mcp_server.py")


class _EmptyRunner:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeMessages:
    def __init__(self) -> None:
        self.captured_kwargs: dict | None = None

    def tool_runner(self, **kwargs):
        self.captured_kwargs = kwargs
        return _EmptyRunner()


class _FakeBeta:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


class _FakeClient:
    def __init__(self) -> None:
        self.beta = _FakeBeta()


async def test_extra_tools_reach_the_tool_runner_call(tmp_path):
    provider = AnthropicProvider.__new__(AnthropicProvider)
    fake_client = _FakeClient()
    provider.client = fake_client

    agent = Agent(
        AgentConfig(name="t", model="claude-haiku-4-5", system_prompt="test"),
        provider=provider,
    )

    async with connect_stdio(sys.executable, [FIXTURE_SERVER]) as mcp_tools:
        await agent.run(
            "goal",
            jail_root=str(tmp_path),
            workdir=str(tmp_path),
            allowed_executables=["echo"],
            extra_tools=mcp_tools,
        )

    tools_passed = fake_client.beta.messages.captured_kwargs["tools"]
    names = {t.name for t in tools_passed}
    assert "run_command" in names, "the agent's own sandbox tool must still be present"
    assert {"echo", "add"} <= names, "external MCP tools must reach the tool_runner call"
