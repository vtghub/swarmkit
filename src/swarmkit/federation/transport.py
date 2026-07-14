"""Signed daemon-to-daemon RPC over HTTP: the minimal-but-real federation
transport described in docs/PLAN.md. Every request carries an ed25519
signature over its own canonical JSON payload; the receiver verifies it
against the sender's registered public key (from its PeerRegistry) before
dispatching anything to its own Rust worker pool. An unsigned, tampered, or
unregistered-peer request is rejected before any subprocess ever runs.

Uses Starlette + uvicorn (both already pulled in by the `mcp` package for
its own Streamable HTTP transport) rather than adding a new HTTP stack.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from swarmkit import _native
from swarmkit.federation.identity import Identity, PeerRegistry, verify

_PAYLOAD_KEYS = ("from_peer", "cmd", "jail_root", "workdir", "allowed_executables", "timeout_secs")


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Deterministic serialization so signing and verification hash the same
    bytes regardless of dict insertion order."""
    return json.dumps(payload, sort_keys=True).encode()


def create_federation_app(pool: "_native.WorkerPool", peer_registry: PeerRegistry) -> Starlette:
    async def handle_task(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "malformed JSON body"}, status_code=400)

        payload = body.get("payload")
        signature = body.get("signature")
        if not isinstance(payload, dict) or not isinstance(signature, str):
            return JSONResponse(
                {"ok": False, "error": "request must be {payload: object, signature: string}"},
                status_code=400,
            )
        if any(key not in payload for key in _PAYLOAD_KEYS[:-1]):  # timeout_secs is optional
            return JSONResponse({"ok": False, "error": "payload missing required fields"}, status_code=400)

        peer = peer_registry.get(payload.get("from_peer", ""))
        if peer is None:
            return JSONResponse(
                {"ok": False, "error": f"unknown peer {payload.get('from_peer')!r}"}, status_code=403
            )

        if not verify(peer.public_key_hex, _canonical_bytes(payload), signature):
            return JSONResponse({"ok": False, "error": "invalid signature"}, status_code=401)

        timeout_secs = float(payload.get("timeout_secs", 30.0))
        try:
            task_id = await pool.submit(
                cmd=payload["cmd"],
                jail_root=payload["jail_root"],
                workdir=payload["workdir"],
                allowed_executables=payload["allowed_executables"],
                timeout_secs=timeout_secs,
            )
        except Exception as e:  # noqa: BLE001 - report to the caller, don't crash the listener
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_secs + 5.0
        while True:
            status = await pool.status(task_id)
            if status["status"] == "completed":
                return JSONResponse({"ok": True, "result": status["result"]})
            if status["status"] == "failed":
                return JSONResponse({"ok": False, "error": status["error"]}, status_code=500)
            if loop.time() > deadline:
                return JSONResponse({"ok": False, "error": "task did not complete in time"}, status_code=504)
            await asyncio.sleep(0.02)

    return Starlette(routes=[Route("/task", handle_task, methods=["POST"])])


def build_federation_server(
    pool: "_native.WorkerPool", peer_registry: PeerRegistry, host: str, port: int
) -> uvicorn.Server:
    """Build (but don't start) a uvicorn Server for the federation app. The
    caller runs it inside its own event loop via `asyncio.create_task(server.serve())`
    and stops it with `server.should_exit = True` — no separate process needed."""
    app = create_federation_app(pool, peer_registry)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", loop="asyncio")
    return uvicorn.Server(config)


async def send_signed_task(
    *,
    host: str,
    port: int,
    identity: Identity,
    from_peer: str,
    cmd: list[str],
    jail_root: str,
    workdir: str,
    allowed_executables: list[str],
    timeout_secs: float = 30.0,
) -> dict[str, Any]:
    """Sign a task request with `identity` and send it to a peer's federation
    endpoint. `from_peer` must be the name this daemon is registered under in
    the *receiver's* PeerRegistry, so the receiver knows whose public key to
    verify against."""
    payload = {
        "from_peer": from_peer,
        "cmd": cmd,
        "jail_root": jail_root,
        "workdir": workdir,
        "allowed_executables": allowed_executables,
        "timeout_secs": timeout_secs,
    }
    signature = identity.sign(_canonical_bytes(payload))

    async with httpx.AsyncClient(timeout=timeout_secs + 10.0) as client:
        response = await client.post(
            f"http://{host}:{port}/task", json={"payload": payload, "signature": signature}
        )
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(body.get("error", f"federation request failed: HTTP {response.status_code}"))
    return body["result"]
