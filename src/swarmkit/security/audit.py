"""Append-only audit log for swarmkitd: every sandboxed subprocess execution
and every Anthropic provider request. Append-only is enforced at the SQLite
layer itself — triggers ABORT any UPDATE/DELETE — not just by API
convention, so it's a real guarantee a test can verify directly against the
database, not just against this module's Python surface. This log is also
the evidence source referenced throughout docs/PLAN.md for "not theater"
verification: if swarmkit claims a tool call happened, it's in here.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from swarmkit.security.secrets import redact

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    event_type TEXT NOT NULL,
    details TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: UPDATE is not allowed');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: DELETE is not allowed');
END;
"""


class AuditLog:
    """Both swarmkitd and any CLI-hosted agent run (`swarmkit run`,
    `swarm run`) open this same file under the shared runtime dir directly —
    there's no RPC for it. SQLite's own file locking handles concurrent
    writers across processes; `busy_timeout` gives a writer a few seconds to
    wait for a lock instead of immediately raising "database is locked"."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._lock = asyncio.Lock()

    async def _insert(self, event_type: str, details: dict[str, Any]) -> None:
        def _do() -> None:
            self._conn.execute(
                "INSERT INTO audit_log (timestamp, event_type, details) VALUES (?, ?, ?)",
                (time.time(), event_type, json.dumps(details)),
            )
            self._conn.commit()

        async with self._lock:
            await asyncio.to_thread(_do)

    async def record_tool_call(self, *, cmd: list[str], result: dict[str, Any]) -> None:
        """Record one sandboxed subprocess execution. stdout/stderr are
        redacted before storage — a command that echoes a secret shouldn't
        leave it sitting in plaintext in a durable log."""
        await self._insert(
            "tool_call",
            {
                "cmd": cmd,
                "pid": result.get("pid"),
                "exit_code": result.get("exit_code"),
                "timed_out": result.get("timed_out"),
                "duration_ms": result.get("duration_ms"),
                "stdout": redact(result.get("stdout", "") or ""),
                "stderr": redact(result.get("stderr", "") or ""),
            },
        )

    async def record_provider_request(
        self,
        *,
        request_id: str | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Record one Anthropic API request — its real request_id and token
        usage, the same fields the Phase 0 real-provider proof tests check."""
        await self._insert(
            "provider_request",
            {
                "request_id": request_id,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        )

    async def record_agent_run(
        self,
        *,
        model: str,
        request_id: str | None,
        input_tokens: int,
        output_tokens: int,
        sandbox_calls: list[dict[str, Any]],
    ) -> None:
        """Convenience: record one full agent run — its provider request plus
        every sandboxed tool call it made. Used both by daemon-hosted agent
        tasks (spawn_agent) and by CLI-hosted single-agent runs, which open
        this same audit.db file directly (see cli/main.py)."""
        await self.record_provider_request(
            request_id=request_id, model=model, input_tokens=input_tokens, output_tokens=output_tokens
        )
        for call in sandbox_calls:
            await self.record_tool_call(cmd=call.get("command", []), result=call)

    async def query(
        self, *, event_type: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        def _do() -> list[tuple[Any, ...]]:
            if event_type:
                cur = self._conn.execute(
                    "SELECT id, timestamp, event_type, details FROM audit_log "
                    "WHERE event_type = ? ORDER BY id DESC LIMIT ?",
                    (event_type, limit),
                )
            else:
                cur = self._conn.execute(
                    "SELECT id, timestamp, event_type, details FROM audit_log "
                    "ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            return cur.fetchall()

        async with self._lock:
            rows = await asyncio.to_thread(_do)
        return [
            {"id": r[0], "timestamp": r[1], "event_type": r[2], "details": json.loads(r[3])}
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()
