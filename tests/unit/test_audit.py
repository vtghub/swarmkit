"""Proves the audit log actually records real events and is append-only at
the SQLite engine level (not just by Python API convention) — a raw UPDATE
or DELETE against the underlying connection must be rejected by a trigger,
not merely unsupported by this module's methods."""

from __future__ import annotations

import sqlite3

import pytest

from swarmkit.security.audit import AuditLog


@pytest.fixture
def log(tmp_path):
    l = AuditLog(tmp_path / "audit.db")
    yield l
    l.close()


async def test_record_and_query_tool_call(log):
    await log.record_tool_call(
        cmd=["echo", "hi"],
        result={"pid": 123, "exit_code": 0, "stdout": "hi\n", "stderr": "", "timed_out": False, "duration_ms": 5},
    )
    entries = await log.query(event_type="tool_call")
    assert len(entries) == 1
    assert entries[0]["details"]["cmd"] == ["echo", "hi"]
    assert entries[0]["details"]["pid"] == 123


async def test_tool_call_stdout_stderr_are_redacted(log):
    await log.record_tool_call(
        cmd=["echo", "sk-ant-api03-" + "x" * 30],
        result={
            "pid": 1,
            "exit_code": 0,
            "stdout": "sk-ant-api03-" + "x" * 30,
            "stderr": "",
            "timed_out": False,
            "duration_ms": 1,
        },
    )
    entries = await log.query(event_type="tool_call")
    assert "sk-ant-" not in entries[0]["details"]["stdout"]
    assert "REDACTED" in entries[0]["details"]["stdout"]


async def test_record_provider_request(log):
    await log.record_provider_request(
        request_id="req_abc123", model="claude-sonnet-5", input_tokens=10, output_tokens=20
    )
    entries = await log.query(event_type="provider_request")
    assert entries[0]["details"]["request_id"] == "req_abc123"
    assert entries[0]["details"]["input_tokens"] == 10


async def test_record_agent_run_writes_provider_and_tool_call_rows(log):
    await log.record_agent_run(
        model="claude-sonnet-5",
        request_id="req_1",
        input_tokens=5,
        output_tokens=6,
        sandbox_calls=[
            {"command": ["ls"], "pid": 1, "exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "duration_ms": 1}
        ],
    )
    all_entries = await log.query()
    types = {e["event_type"] for e in all_entries}
    assert types == {"provider_request", "tool_call"}


async def test_query_respects_limit_and_order(log):
    for i in range(5):
        await log.record_provider_request(
            request_id=f"req_{i}", model="claude-sonnet-5", input_tokens=1, output_tokens=1
        )
    entries = await log.query(limit=2)
    assert len(entries) == 2
    # most recent first
    assert entries[0]["details"]["request_id"] == "req_4"
    assert entries[1]["details"]["request_id"] == "req_3"


async def test_update_is_rejected_at_the_sqlite_layer(log):
    await log.record_provider_request(
        request_id="req_x", model="claude-sonnet-5", input_tokens=1, output_tokens=1
    )
    with pytest.raises(sqlite3.IntegrityError):
        log._conn.execute("UPDATE audit_log SET event_type = 'tampered' WHERE id = 1")


async def test_delete_is_rejected_at_the_sqlite_layer(log):
    await log.record_provider_request(
        request_id="req_y", model="claude-sonnet-5", input_tokens=1, output_tokens=1
    )
    with pytest.raises(sqlite3.IntegrityError):
        log._conn.execute("DELETE FROM audit_log WHERE id = 1")


async def test_two_auditlog_handles_on_same_file_both_see_writes(tmp_path):
    """Simulates the real multi-process setup: swarmkitd and a CLI-hosted
    agent run both open the same audit.db file directly."""
    path = tmp_path / "shared_audit.db"
    a = AuditLog(path)
    b = AuditLog(path)
    try:
        await a.record_provider_request(
            request_id="from_a", model="claude-sonnet-5", input_tokens=1, output_tokens=1
        )
        entries = await b.query()
        assert any(e["details"]["request_id"] == "from_a" for e in entries)
    finally:
        a.close()
        b.close()
