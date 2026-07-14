"""Proves the coordinator's self-learning loop end to end without a live
LLM: a verified subtask (1) retrieves a relevant past lesson and folds it
into the agent's actual prompt, and (2) records its own real (quorum-
verified) outcome afterward — never for unverified subtasks, where no real
success/failure signal exists."""

from __future__ import annotations

from swarmkit._native import VectorStore
from swarmkit.agents.catalog import AgentCatalog
from swarmkit.core.providers.anthropic_provider import AnthropicProvider
from swarmkit.memory.embeddings import HashingEmbedder
from swarmkit.memory.trajectories import TrajectoryStore
from swarmkit.swarm.coordinator import Coordinator, Subtask


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


def _fake_provider() -> tuple[AnthropicProvider, _FakeClient]:
    provider = AnthropicProvider.__new__(AnthropicProvider)
    fake_client = _FakeClient()
    provider.client = fake_client
    return provider, fake_client


def _trajectory_store(tmp_path) -> TrajectoryStore:
    return TrajectoryStore(tmp_path / "trajectories.db", VectorStore(), HashingEmbedder(dim=64))


async def test_verified_subtask_retrieves_a_past_lesson_and_folds_it_into_the_prompt(tmp_path):
    provider, fake_client = _fake_provider()
    store = _trajectory_store(tmp_path)
    try:
        await store.record_run(
            agent_name="coder",
            goal="fix the widget",
            outcome="failure",
            sandbox_calls=[
                {"command": ["pytest"], "exit_code": 1, "stdout": "", "stderr": "boom", "timed_out": False}
            ],
        )

        async def executor(command: list[str]) -> dict:
            return {"exit_code": 0, "stdout": "ok", "stderr": "", "pid": 1, "timed_out": False, "duration_ms": 1}

        coordinator = Coordinator(AgentCatalog(), provider=provider, trajectories=store)
        subtask = Subtask(id="s1", agent="coder", goal="fix the widget again", verify=True, verify_command=["pytest"])

        await coordinator._run_subtask(
            subtask, executor, jail_root=str(tmp_path), workdir=str(tmp_path), allowed_executables=["pytest"]
        )

        content = fake_client.beta.messages.captured_kwargs["messages"][0]["content"]
        assert "boom" in content, "the prior failure's lesson must reach the agent's actual prompt"
        assert content.endswith("Your goal: fix the widget again")
    finally:
        store.close()


async def test_verified_subtask_records_its_real_quorum_outcome(tmp_path):
    provider, _fake_client = _fake_provider()
    store = _trajectory_store(tmp_path)
    try:

        async def executor(command: list[str]) -> dict:
            return {"exit_code": 0, "stdout": "ok", "stderr": "", "pid": 1, "timed_out": False, "duration_ms": 1}

        coordinator = Coordinator(AgentCatalog(), provider=provider, trajectories=store)
        subtask = Subtask(id="s1", agent="coder", goal="add a retry helper", verify=True, verify_command=["pytest"])

        result = await coordinator._run_subtask(
            subtask, executor, jail_root=str(tmp_path), workdir=str(tmp_path), allowed_executables=["pytest"]
        )

        assert result.success is True
        recorded = await store.retrieve_relevant("coder", "add a retry helper")
        assert len(recorded) == 1
        assert recorded[0].outcome == "success"
        assert recorded[0].goal == "add a retry helper"
    finally:
        store.close()


async def test_unverified_subtask_never_records_a_trajectory(tmp_path):
    """No verify_command means no real success/failure signal — recording
    one anyway would be exactly the kind of guessed outcome this project's
    'no theater' rule forbids."""
    provider, _fake_client = _fake_provider()
    store = _trajectory_store(tmp_path)
    try:

        async def executor(command: list[str]) -> dict:
            raise AssertionError("verify_command should never run for an unverified subtask")

        coordinator = Coordinator(AgentCatalog(), provider=provider, trajectories=store)
        subtask = Subtask(id="s1", agent="coder", goal="write some docs", verify=False, verify_command=None)

        result = await coordinator._run_subtask(
            subtask, executor, jail_root=str(tmp_path), workdir=str(tmp_path), allowed_executables=[]
        )

        assert result.success is True
        assert result.quorum is None
        assert await store.retrieve_relevant("coder", "write some docs") == []
    finally:
        store.close()
