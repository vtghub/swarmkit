"""Thin async client for talking to swarmkitd over its Unix domain socket. No
logic lives here beyond the wire protocol — the daemon does the real work."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from swarmkit.daemon.server import socket_path


class DaemonUnavailable(RuntimeError):
    pass


async def _request(payload: dict[str, Any]) -> dict[str, Any]:
    sock = socket_path()
    if not sock.exists():
        raise DaemonUnavailable(
            "swarmkitd is not running. Start it first: swarmkit daemon start"
        )
    try:
        reader, writer = await asyncio.open_unix_connection(str(sock))
    except OSError as e:
        raise DaemonUnavailable(f"could not connect to swarmkitd: {e}") from e
    try:
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()
        line = await reader.readline()
        if not line:
            raise DaemonUnavailable("swarmkitd closed the connection without responding")
        return json.loads(line.decode())
    finally:
        writer.close()


async def ping() -> dict[str, Any]:
    return await _request({"cmd": "ping"})


async def submit_task(
    cmd: list[str],
    *,
    jail_root: str,
    workdir: str,
    allowed_executables: list[str],
    timeout_secs: float = 30.0,
) -> str:
    response = await _request(
        {
            "cmd": "submit_task",
            "args": {
                "cmd": cmd,
                "jail_root": jail_root,
                "workdir": workdir,
                "allowed_executables": allowed_executables,
                "timeout_secs": timeout_secs,
            },
        }
    )
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "submit_task failed"))
    return response["task_id"]


async def task_status(task_id: str) -> dict[str, Any] | None:
    response = await _request({"cmd": "task_status", "task_id": task_id})
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "task_status failed"))
    return response["status"]


async def list_tasks() -> list[Any]:
    response = await _request({"cmd": "list_tasks"})
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "list_tasks failed"))
    return response["tasks"]


async def run_command(
    cmd: list[str],
    *,
    jail_root: str,
    workdir: str,
    allowed_executables: list[str],
    timeout_secs: float = 30.0,
    poll_interval: float = 0.05,
) -> dict[str, Any]:
    """Submit a command to the daemon's worker pool and poll until it's done.
    This is the daemon-mediated replacement for calling _native.run_sandboxed
    directly — the sandboxed subprocess actually runs inside swarmkitd."""
    task_id = await submit_task(
        cmd,
        jail_root=jail_root,
        workdir=workdir,
        allowed_executables=allowed_executables,
        timeout_secs=timeout_secs,
    )
    while True:
        status = await task_status(task_id)
        kind = status.get("status") if status else None
        if kind in ("completed", "failed"):
            return status
        await asyncio.sleep(poll_interval)


async def spawn_agent(
    agent_name: str,
    goal: str,
    *,
    jail_root: str,
    workdir: str,
    allowed_executables: list[str],
) -> str:
    """Spawn a catalog agent on `goal`, running inside swarmkitd. Returns a
    task_id immediately — the agent's own tool calls run through the same
    Rust worker pool as submit_task, but the LLM conversation itself runs as
    an asyncio task in the daemon (see daemon/agent_tasks.py)."""
    response = await _request(
        {
            "cmd": "spawn_agent",
            "args": {
                "agent_name": agent_name,
                "goal": goal,
                "jail_root": jail_root,
                "workdir": workdir,
                "allowed_executables": allowed_executables,
            },
        }
    )
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "spawn_agent failed"))
    return response["task_id"]


async def agent_task_status(task_id: str) -> dict[str, Any] | None:
    response = await _request({"cmd": "agent_task_status", "task_id": task_id})
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "agent_task_status failed"))
    return response["status"]
