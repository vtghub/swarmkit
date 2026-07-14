"""Trajectory memory: outcome-tagged records of past agent runs, retrieved
before future runs so an agent can draw on its own history instead of
starting from a blank slate every time.

This is the concrete, verifiable shape "self-learning" takes for a
stateless LLM call: no weight updates, no fine-tuning. A trajectory is
recorded only when a real success/failure signal exists (a quorum-verified
subtask's `verify_command` result — see swarm/coordinator.py — never a
guess), and its lesson is derived mechanically from what the run actually
did: the failing command and its stderr, or the sequence of commands that
succeeded. Retrieval reuses the same RRF-fused keyword+vector search as
memory/rag.py, and the results are folded into the next run's prompt as
concrete precedent (agents/base.py's `context_hints`) — an observable
change to the agent's actual input, not a hidden internal state update.

Known limitation: the underlying VectorStore has no per-agent filtering, so
`retrieve_relevant`'s vector leg searches across every agent's trajectories
and filters by `agent_name` after the fact. For the trajectory volumes a
single project accumulates this is a reasonable approximation, not an exact
per-agent nearest-neighbor search — worth revisiting if that stops holding.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from swarmkit._native import VectorStore
from swarmkit.memory.embeddings import Embedder
from swarmkit.memory.rag import reciprocal_rank_fusion

Outcome = Literal["success", "failure"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS trajectories (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    goal TEXT NOT NULL,
    outcome TEXT NOT NULL,
    lesson TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS trajectories_fts USING fts5(
    goal,
    content='trajectories',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS trajectories_ai AFTER INSERT ON trajectories BEGIN
    INSERT INTO trajectories_fts(rowid, goal) VALUES (new.rowid, new.goal);
END;

CREATE TRIGGER IF NOT EXISTS trajectories_ad AFTER DELETE ON trajectories BEGIN
    INSERT INTO trajectories_fts(trajectories_fts, rowid, goal) VALUES ('delete', old.rowid, old.goal);
END;
"""


@dataclass
class TrajectoryRecord:
    id: str
    agent_name: str
    goal: str
    outcome: Outcome
    lesson: str
    created_at: float


def derive_lesson(
    outcome: Outcome,
    sandbox_calls: list[dict[str, Any]],
    *,
    fallback: str = "",
) -> str:
    """Mechanically derive a lesson from what a run actually did — the first
    failing command and its stderr for a failure, or the sequence of
    commands that ran for a success. No LLM call and no summarization guess:
    just the concrete facts already recorded in sandbox_calls."""
    if outcome == "failure":
        for call in sandbox_calls:
            timed_out = bool(call.get("timed_out"))
            exit_code = call.get("exit_code")
            if timed_out or exit_code not in (0, None):
                command = " ".join(str(c) for c in call.get("command", []))
                detail = "timed out" if timed_out else f"exit code {exit_code}"
                stderr = (call.get("stderr") or "").strip()[:200]
                return f'ran `{command}` which {detail}' + (f": {stderr}" if stderr else "")
        return fallback or "failed; no failing sandboxed command was recorded"

    commands = [" ".join(str(c) for c in call.get("command", [])) for call in sandbox_calls if call.get("command")]
    if commands:
        return "succeeded using: " + "; then ".join(commands)
    return fallback or "succeeded; no sandboxed command was recorded"


def format_hint(record: TrajectoryRecord) -> str:
    """Render one trajectory as a single line of prompt-ready context."""
    verdict = "succeeded" if record.outcome == "success" else "failed"
    return f'On a similar goal ("{record.goal}"), a previous attempt {verdict}: {record.lesson}'


class TrajectoryStore:
    """Durable, retrievable record of past agent runs. Same pattern as
    memory/store.py + memory/vectors.py (SQLite + paired VectorStore) but
    with outcome/lesson/agent_name as first-class columns instead of opaque
    memory text."""

    def __init__(self, db_path: str | Path, vectors: VectorStore, embedder: Embedder) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._lock = asyncio.Lock()
        self.vectors = vectors
        self.embedder = embedder

    async def record(
        self,
        *,
        agent_name: str,
        goal: str,
        outcome: Outcome,
        lesson: str,
    ) -> TrajectoryRecord:
        record = TrajectoryRecord(
            id=str(uuid.uuid4()),
            agent_name=agent_name,
            goal=goal,
            outcome=outcome,
            lesson=lesson,
            created_at=time.time(),
        )

        def _insert() -> None:
            self._conn.execute(
                "INSERT INTO trajectories (id, agent_name, goal, outcome, lesson, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (record.id, record.agent_name, record.goal, record.outcome, record.lesson, record.created_at),
            )
            self._conn.commit()

        async with self._lock:
            await asyncio.to_thread(_insert)
        [vector] = self.embedder.embed([goal])
        self.vectors.add(record.id, vector)
        return record

    async def record_run(
        self,
        *,
        agent_name: str,
        goal: str,
        outcome: Outcome,
        sandbox_calls: list[dict[str, Any]],
        lesson: str | None = None,
    ) -> TrajectoryRecord:
        """Convenience: derive the lesson mechanically from sandbox_calls
        unless the caller supplies a better one (e.g. a verify_command's own
        failure output, which isn't visible from inside Agent.run)."""
        return await self.record(
            agent_name=agent_name,
            goal=goal,
            outcome=outcome,
            lesson=lesson or derive_lesson(outcome, sandbox_calls),
        )

    def _get_many(self, ids: list[str]) -> dict[str, TrajectoryRecord]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self._conn.execute(
            f"SELECT id, agent_name, goal, outcome, lesson, created_at FROM trajectories "
            f"WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        return {
            r[0]: TrajectoryRecord(
                id=r[0], agent_name=r[1], goal=r[2], outcome=r[3], lesson=r[4], created_at=r[5]
            )
            for r in rows
        }

    async def retrieve_relevant(
        self,
        agent_name: str,
        goal: str,
        *,
        k: int = 3,
        candidates: int = 50,
    ) -> list[TrajectoryRecord]:
        """Hybrid keyword+vector retrieval over past goals, most-relevant
        first, restricted to `agent_name`. Empty (not an error) if this
        agent has no recorded trajectories yet."""

        def _keyword() -> list[str]:
            rows = self._conn.execute(
                """
                SELECT t.id
                FROM trajectories_fts
                JOIN trajectories t ON t.rowid = trajectories_fts.rowid
                WHERE trajectories_fts MATCH ? AND t.agent_name = ?
                ORDER BY bm25(trajectories_fts)
                LIMIT ?
                """,
                (goal, agent_name, candidates),
            ).fetchall()
            return [r[0] for r in rows]

        async with self._lock:
            keyword_ids = await asyncio.to_thread(_keyword)

        if len(self.vectors) == 0:
            vector_ids: list[str] = []
        else:
            [query_vector] = self.embedder.embed([goal])
            vector_ids = [mem_id for mem_id, _ in self.vectors.search(query_vector, k=candidates)]

        fused = reciprocal_rank_fusion(keyword_ids, vector_ids)
        if not fused:
            return []

        records = self._get_many(list(fused.keys()))
        relevant_ids = [mid for mid in fused if mid in records and records[mid].agent_name == agent_name]
        return [records[mid] for mid in relevant_ids[:k]]

    def close(self) -> None:
        self._conn.close()
