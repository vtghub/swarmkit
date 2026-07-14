"""Proves the signed federation transport rejects everything it should
before ever touching the worker pool, and dispatches a real sandboxed
subprocess for a legitimately signed, registered-peer request. Runs the
Starlette app in-process (ASGI transport, no real socket) for speed; the
integration test covers two real swarmkitd OS processes end to end."""

from __future__ import annotations

import httpx
import pytest

from swarmkit import _native
from swarmkit.federation.identity import PeerRegistry, load_or_create_identity
from swarmkit.federation.transport import _canonical_bytes, create_federation_app


@pytest.fixture
def pool():
    return _native.WorkerPool(concurrency=2)


@pytest.fixture
def registry(tmp_path):
    return PeerRegistry(tmp_path / "peers.json")


async def _post(app, payload: dict, signature: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/task", json={"payload": payload, "signature": signature})


async def test_legit_signed_request_runs_a_real_subprocess(pool, registry, tmp_path):
    identity = load_or_create_identity(tmp_path / "id.key")
    registry.add("alice", "127.0.0.1", 0, identity.public_key_hex)
    app = create_federation_app(pool, registry)

    payload = {
        "from_peer": "alice",
        "cmd": ["echo", "federated-hello"],
        "jail_root": str(tmp_path),
        "workdir": str(tmp_path),
        "allowed_executables": ["echo"],
        "timeout_secs": 5.0,
    }
    signature = identity.sign(_canonical_bytes(payload))

    response = await _post(app, payload, signature)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["result"]["exit_code"] == 0
    assert "federated-hello" in body["result"]["stdout"]
    assert body["result"]["pid"] > 0


async def test_unregistered_peer_is_rejected(pool, registry, tmp_path):
    identity = load_or_create_identity(tmp_path / "id.key")
    app = create_federation_app(pool, registry)

    payload = {
        "from_peer": "nobody",
        "cmd": ["echo", "hi"],
        "jail_root": str(tmp_path),
        "workdir": str(tmp_path),
        "allowed_executables": ["echo"],
    }
    signature = identity.sign(_canonical_bytes(payload))

    response = await _post(app, payload, signature)
    assert response.status_code == 403
    assert response.json()["ok"] is False


async def test_wrong_identity_signature_is_rejected(pool, registry, tmp_path):
    real = load_or_create_identity(tmp_path / "real.key")
    impostor = load_or_create_identity(tmp_path / "impostor.key")
    registry.add("alice", "127.0.0.1", 0, real.public_key_hex)
    app = create_federation_app(pool, registry)

    payload = {
        "from_peer": "alice",
        "cmd": ["echo", "hi"],
        "jail_root": str(tmp_path),
        "workdir": str(tmp_path),
        "allowed_executables": ["echo"],
    }
    signature = impostor.sign(_canonical_bytes(payload))

    response = await _post(app, payload, signature)
    assert response.status_code == 401
    assert response.json()["ok"] is False


async def test_tampered_payload_after_signing_is_rejected(pool, registry, tmp_path):
    identity = load_or_create_identity(tmp_path / "id.key")
    registry.add("alice", "127.0.0.1", 0, identity.public_key_hex)
    app = create_federation_app(pool, registry)

    payload = {
        "from_peer": "alice",
        "cmd": ["echo", "hi"],
        "jail_root": str(tmp_path),
        "workdir": str(tmp_path),
        "allowed_executables": ["echo"],
    }
    signature = identity.sign(_canonical_bytes(payload))
    payload["cmd"] = ["rm", "-rf", "/"]  # tamper after signing

    response = await _post(app, payload, signature)
    assert response.status_code == 401
    assert response.json()["ok"] is False


async def test_disallowed_command_is_rejected_before_execution(pool, registry, tmp_path):
    """The Rust sandbox's allowlist check runs before any subprocess is
    spawned (see crates/swarmkit-core/src/sandbox.rs); it surfaces here as an
    async task failure (the pool accepted submission, then the task failed
    pre-spawn), not a synchronous submit()-time rejection."""
    identity = load_or_create_identity(tmp_path / "id.key")
    registry.add("alice", "127.0.0.1", 0, identity.public_key_hex)
    app = create_federation_app(pool, registry)

    payload = {
        "from_peer": "alice",
        "cmd": ["rm", "-rf", "/"],
        "jail_root": str(tmp_path),
        "workdir": str(tmp_path),
        "allowed_executables": ["echo"],
    }
    signature = identity.sign(_canonical_bytes(payload))

    response = await _post(app, payload, signature)
    assert response.status_code == 500
    body = response.json()
    assert body["ok"] is False
    assert "allowlist" in body["error"]


async def test_malformed_body_is_rejected(pool, registry):
    app = create_federation_app(pool, registry)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/task", content=b"not json")
    assert response.status_code == 400


async def test_missing_required_field_is_rejected(pool, registry, tmp_path):
    identity = load_or_create_identity(tmp_path / "id.key")
    registry.add("alice", "127.0.0.1", 0, identity.public_key_hex)
    app = create_federation_app(pool, registry)

    payload = {"from_peer": "alice", "cmd": ["echo", "hi"]}  # missing jail_root etc.
    signature = identity.sign(_canonical_bytes(payload))
    response = await _post(app, payload, signature)
    assert response.status_code == 400
