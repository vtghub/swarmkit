"""SQLite-backed structured memory: durable storage for memory text content,
with an FTS5 shadow table for keyword search. sqlite3 is synchronous, so
every call runs on a worker thread via asyncio.to_thread and is serialized
behind a lock — that keeps the daemon's event loop from ever blocking on disk
I/O without needing a second sqlite connection per thread.

Only `memories` ships in Phase 2. `tasks`/`agent_runs`/`tool_calls`/`entities`
(from docs/PLAN.md's structured-storage list) get added when the phases that
actually consume them — swarm coordination, security audit logging — land.
An unused table is dead schema, not a feature.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;
"""


@dataclass
class MemoryRecord:
    id: str
    content: str
    created_at: float


class MemoryStore:
    """Durable text storage + keyword search for memory entries. Embeddings
    for the same ids live in a paired VectorStore (memory/vectors.py) — this
    class owns only the text and its FTS5 index."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._lock = asyncio.Lock()

    async def add(self, content: str, *, memory_id: str | None = None) -> MemoryRecord:
        record = MemoryRecord(
            id=memory_id or str(uuid.uuid4()), content=content, created_at=time.time()
        )

        def _insert() -> None:
            self._conn.execute(
                "INSERT INTO memories (id, content, created_at) VALUES (?, ?, ?)",
                (record.id, record.content, record.created_at),
            )
            self._conn.commit()

        async with self._lock:
            await asyncio.to_thread(_insert)
        return record

    async def get(self, memory_id: str) -> MemoryRecord | None:
        def _select() -> tuple | None:
            return self._conn.execute(
                "SELECT id, content, created_at FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()

        async with self._lock:
            row = await asyncio.to_thread(_select)
        return MemoryRecord(id=row[0], content=row[1], created_at=row[2]) if row else None

    async def keyword_search(self, query: str, k: int = 10) -> list[tuple[MemoryRecord, float]]:
        """FTS5 keyword search ranked by bm25 (lower bm25 = more relevant)."""

        def _select() -> list[tuple]:
            return self._conn.execute(
                """
                SELECT m.id, m.content, m.created_at, bm25(memories_fts) AS rank
                FROM memories_fts
                JOIN memories m ON m.rowid = memories_fts.rowid
                WHERE memories_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, k),
            ).fetchall()

        async with self._lock:
            rows = await asyncio.to_thread(_select)
        return [(MemoryRecord(id=r[0], content=r[1], created_at=r[2]), r[3]) for r in rows]

    def close(self) -> None:
        self._conn.close()
