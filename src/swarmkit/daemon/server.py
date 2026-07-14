"""swarmkitd — owns the Rust worker pool and serves task submission/status over
a Unix domain socket. This is the process that actually dispatches sandboxed
subprocess execution; the CLI (via daemon_client) is a thin client to it.

Paths are computed from SWARMKIT_RUNTIME_DIR at call time, not frozen at
import time, so tests can point a fresh daemon+client pair at an isolated
directory just by setting the env var before use.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any

from swarmkit import _native
from swarmkit.agents.base import Executor
from swarmkit.agents.catalog import AgentCatalog
from swarmkit.daemon.agent_tasks import AgentTaskRegistry

DEFAULT_CONCURRENCY = 8


def _pool_executor(
    pool: "_native.WorkerPool",
    jail_root: str,
    workdir: str,
    allowed_executables: list[str],
    timeout_secs: float = 30.0,
) -> Executor:
    """An in-process equivalent of daemon_client.run_command: submits to the
    given WorkerPool and polls until done, without a socket round-trip (used
    for tool calls made by agent tasks running inside the daemon itself)."""

    async def _execute(command: list[str]) -> dict[str, Any]:
        task_id = await pool.submit(
            cmd=command,
            jail_root=jail_root,
            workdir=workdir,
            allowed_executables=allowed_executables,
            timeout_secs=timeout_secs,
        )
        while True:
            status = await pool.status(task_id)
            if status["status"] == "completed":
                return status["result"]
            if status["status"] == "failed":
                raise RuntimeError(status["error"])
            await asyncio.sleep(0.02)

    return _execute


def runtime_dir() -> Path:
    return Path(os.environ.get("SWARMKIT_RUNTIME_DIR", str(Path.home() / ".swarmkit")))


def socket_path() -> Path:
    return runtime_dir() / "daemon.sock"


def pid_path() -> Path:
    return runtime_dir() / "daemon.pid"


class Daemon:
    """Wraps one native WorkerPool (sandboxed command tasks) and one
    AgentTaskRegistry (LLM-driven agent-run tasks) and answers JSON-line
    requests about both."""

    def __init__(self, concurrency: int = DEFAULT_CONCURRENCY) -> None:
        self.pool = _native.WorkerPool(concurrency)
        self.catalog = AgentCatalog()
        self.agent_tasks = AgentTaskRegistry(self.catalog)

    async def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        cmd = request.get("cmd")
        if cmd == "ping":
            return {"ok": True, "pid": os.getpid()}
        if cmd == "submit_task":
            args = request["args"]
            task_id = await self.pool.submit(
                cmd=args["cmd"],
                jail_root=args["jail_root"],
                workdir=args["workdir"],
                allowed_executables=args["allowed_executables"],
                timeout_secs=args.get("timeout_secs", 30.0),
            )
            return {"ok": True, "task_id": task_id}
        if cmd == "task_status":
            status = await self.pool.status(request["task_id"])
            return {"ok": True, "status": status}
        if cmd == "list_tasks":
            tasks = await self.pool.list_tasks()
            return {"ok": True, "tasks": tasks}
        if cmd == "spawn_agent":
            args = request["args"]
            executor = _pool_executor(
                self.pool, args["jail_root"], args["workdir"], args["allowed_executables"]
            )
            task_id = self.agent_tasks.submit(
                agent_name=args["agent_name"],
                goal=args["goal"],
                jail_root=args["jail_root"],
                workdir=args["workdir"],
                allowed_executables=args["allowed_executables"],
                executor=executor,
            )
            return {"ok": True, "task_id": task_id}
        if cmd == "agent_task_status":
            status = self.agent_tasks.status(request["task_id"])
            if status is None:
                return {"ok": True, "status": None}
            return {
                "ok": True,
                "status": {"status": status.status, "result": status.result, "error": status.error},
            }
        return {"ok": False, "error": f"unknown command: {cmd!r}"}


async def _handle_conn(
    daemon: Daemon, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    try:
        line = await reader.readline()
        if not line:
            return
        request = json.loads(line.decode())
        response = await daemon.handle_request(request)
    except Exception as e:  # noqa: BLE001 - report to the client, don't crash the daemon
        response = {"ok": False, "error": str(e)}
    try:
        writer.write((json.dumps(response) + "\n").encode())
        await writer.drain()
    finally:
        writer.close()


async def serve(concurrency: int = DEFAULT_CONCURRENCY) -> None:
    runtime_dir().mkdir(parents=True, exist_ok=True)
    sock = socket_path()
    if sock.exists():
        sock.unlink()

    daemon = Daemon(concurrency)
    server = await asyncio.start_unix_server(
        lambda r, w: _handle_conn(daemon, r, w), path=str(sock)
    )
    pid_path().write_text(str(os.getpid()))

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        async with server:
            await stop_event.wait()
    finally:
        sock.unlink(missing_ok=True)
        pid_path().unlink(missing_ok=True)


def main() -> None:
    concurrency = int(os.environ.get("SWARMKIT_CONCURRENCY", DEFAULT_CONCURRENCY))
    asyncio.run(serve(concurrency))


if __name__ == "__main__":
    main()
