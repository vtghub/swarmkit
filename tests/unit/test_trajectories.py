"""Proves memory/trajectories.py's real mechanism for self-learning: a
lesson is derived mechanically from what a run actually did (no LLM call,
no guessing), records persist and survive a process restart, and retrieval
returns the agent's own past attempts ranked by relevance to a new goal."""

from __future__ import annotations

from swarmkit._native import VectorStore
from swarmkit.memory.embeddings import HashingEmbedder
from swarmkit.memory.trajectories import TrajectoryStore, derive_lesson, format_hint


def test_derive_lesson_for_a_failure_names_the_failing_command_and_stderr():
    sandbox_calls = [
        {"command": ["ls"], "exit_code": 0, "stdout": "a.txt\n", "stderr": "", "timed_out": False},
        {"command": ["cat", "missing.txt"], "exit_code": 1, "stdout": "", "stderr": "No such file", "timed_out": False},
    ]
    lesson = derive_lesson("failure", sandbox_calls)
    assert "cat missing.txt" in lesson
    assert "exit code 1" in lesson
    assert "No such file" in lesson


def test_derive_lesson_for_a_timeout_names_it_as_such():
    sandbox_calls = [{"command": ["sleep", "60"], "exit_code": None, "stdout": "", "stderr": "", "timed_out": True}]
    lesson = derive_lesson("failure", sandbox_calls)
    assert "sleep 60" in lesson
    assert "timed out" in lesson


def test_derive_lesson_for_a_failure_with_no_recorded_calls_uses_fallback():
    assert derive_lesson("failure", []) == "failed; no failing sandboxed command was recorded"
    assert derive_lesson("failure", [], fallback="verify_command failed") == "verify_command failed"


def test_derive_lesson_for_a_success_names_the_commands_that_ran():
    sandbox_calls = [
        {"command": ["mkdir", "-p", "out"], "exit_code": 0, "stdout": "", "stderr": "", "timed_out": False},
        {"command": ["touch", "out/f.txt"], "exit_code": 0, "stdout": "", "stderr": "", "timed_out": False},
    ]
    lesson = derive_lesson("success", sandbox_calls)
    assert lesson == "succeeded using: mkdir -p out; then touch out/f.txt"


def test_format_hint_includes_goal_outcome_and_lesson():
    from swarmkit.memory.trajectories import TrajectoryRecord

    record = TrajectoryRecord(
        id="1", agent_name="coder", goal="fix the parser", outcome="failure", lesson="forgot a flag", created_at=0.0
    )
    hint = format_hint(record)
    assert "fix the parser" in hint
    assert "failed" in hint
    assert "forgot a flag" in hint


def _store(tmp_path, name: str = "trajectories") -> TrajectoryStore:
    return TrajectoryStore(
        tmp_path / f"{name}.db",
        VectorStore(),
        HashingEmbedder(dim=64),
    )


async def test_record_and_retrieve_relevant_returns_matching_agent_trajectory(tmp_path):
    store = _store(tmp_path)
    try:
        await store.record_run(
            agent_name="coder",
            goal="add error handling to the parser",
            outcome="failure",
            sandbox_calls=[{"command": ["pytest"], "exit_code": 1, "stdout": "", "stderr": "AssertionError", "timed_out": False}],
        )
        results = await store.retrieve_relevant("coder", "add error handling to the tokenizer")
        assert len(results) == 1
        assert results[0].agent_name == "coder"
        assert results[0].outcome == "failure"
        assert "AssertionError" in results[0].lesson
    finally:
        store.close()


async def test_retrieve_relevant_is_scoped_to_the_named_agent(tmp_path):
    store = _store(tmp_path)
    try:
        await store.record_run(
            agent_name="coder", goal="fix the bug", outcome="success", sandbox_calls=[{"command": ["pytest"], "exit_code": 0, "stdout": "", "stderr": ""}]
        )
        await store.record_run(
            agent_name="reviewer", goal="fix the bug", outcome="failure", sandbox_calls=[]
        )
        results = await store.retrieve_relevant("tester", "fix the bug")
        assert results == []
    finally:
        store.close()


async def test_retrieve_relevant_on_an_empty_store_returns_empty(tmp_path):
    store = _store(tmp_path)
    try:
        assert await store.retrieve_relevant("coder", "anything") == []
    finally:
        store.close()


async def test_trajectories_persist_across_a_simulated_restart(tmp_path):
    db_path = tmp_path / "trajectories.db"
    vectors_path = tmp_path / "trajectories.bin"
    embedder = HashingEmbedder(dim=64)

    first_vectors = VectorStore()
    first = TrajectoryStore(db_path, first_vectors, embedder)
    await first.record_run(
        agent_name="coder",
        goal="implement retries for the http client",
        outcome="success",
        sandbox_calls=[{"command": ["pytest", "-k", "retry"], "exit_code": 0, "stdout": "", "stderr": ""}],
    )
    first_vectors.save(str(vectors_path))
    first.close()

    second_vectors = VectorStore.load(str(vectors_path))
    second = TrajectoryStore(db_path, second_vectors, embedder)
    try:
        results = await second.retrieve_relevant("coder", "add retry logic to the http client")
        assert len(results) == 1
        assert "pytest -k retry" in results[0].lesson
    finally:
        second.close()
