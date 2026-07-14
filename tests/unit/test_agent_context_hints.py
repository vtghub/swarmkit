"""Proves context_hints actually change what reaches the model — the
real, observable mechanism trajectory learning depends on — verified
without a live LLM by capturing the kwargs a fake Anthropic client
receives (same pattern as test_agent_extra_tools.py)."""

from __future__ import annotations

from swarmkit.agents.base import Agent, AgentConfig
from swarmkit.core.providers.anthropic_provider import AnthropicProvider


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


def _agent() -> tuple[Agent, _FakeClient]:
    provider = AnthropicProvider.__new__(AnthropicProvider)
    fake_client = _FakeClient()
    provider.client = fake_client
    agent = Agent(AgentConfig(name="t", model="claude-haiku-4-5", system_prompt="test"), provider=provider)
    return agent, fake_client


async def test_no_hints_sends_the_goal_unchanged(tmp_path):
    agent, fake_client = _agent()
    await agent.run(
        "fix the bug", jail_root=str(tmp_path), workdir=str(tmp_path), allowed_executables=["echo"]
    )
    messages = fake_client.beta.messages.captured_kwargs["messages"]
    assert messages == [{"role": "user", "content": "fix the bug"}]


async def test_context_hints_are_prepended_to_the_actual_message_sent(tmp_path):
    agent, fake_client = _agent()
    await agent.run(
        "fix the bug",
        jail_root=str(tmp_path),
        workdir=str(tmp_path),
        allowed_executables=["echo"],
        context_hints=[
            "a previous attempt failed: ran `pytest` which exited 1: AssertionError",
            "a previous attempt succeeded using: pytest -x",
        ],
    )
    content = fake_client.beta.messages.captured_kwargs["messages"][0]["content"]
    assert "a previous attempt failed" in content
    assert "a previous attempt succeeded" in content
    assert content.endswith("Your goal: fix the bug")


async def test_empty_hints_list_behaves_like_no_hints(tmp_path):
    agent, fake_client = _agent()
    await agent.run(
        "fix the bug",
        jail_root=str(tmp_path),
        workdir=str(tmp_path),
        allowed_executables=["echo"],
        context_hints=[],
    )
    messages = fake_client.beta.messages.captured_kwargs["messages"]
    assert messages == [{"role": "user", "content": "fix the bug"}]
