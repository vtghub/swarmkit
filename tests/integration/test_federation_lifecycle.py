"""Phase 5's stated 'done' criterion: two local daemons complete a signed
cross-daemon task. Starts two real swarmkitd OS processes (not in-process
simulacra), registers each as the other's peer via the CLI-facing
PeerRegistry, and sends a real signed task over HTTP between them."""

from __future__ import annotations

import shutil
import tempfile

import pytest

from swarmkit.daemon import supervisor
from swarmkit.daemon.server import identity_key_path, peers_path
from swarmkit.federation.identity import PeerRegistry, load_or_create_identity
from swarmkit.federation.transport import send_signed_task


@pytest.fixture
def two_daemons(monkeypatch):
    dir_a = tempfile.mkdtemp(prefix="swarmkit-fed-a-")
    dir_b = tempfile.mkdtemp(prefix="swarmkit-fed-b-")

    monkeypatch.setenv("SWARMKIT_RUNTIME_DIR", dir_a)
    id_a = load_or_create_identity(identity_key_path())
    PeerRegistry(peers_path())  # ensure file exists

    monkeypatch.setenv("SWARMKIT_RUNTIME_DIR", dir_b)
    id_b = load_or_create_identity(identity_key_path())

    monkeypatch.setenv("SWARMKIT_RUNTIME_DIR", dir_a)
    PeerRegistry(peers_path()).add("peer_b", "127.0.0.1", 19102, id_b.public_key_hex)

    monkeypatch.setenv("SWARMKIT_RUNTIME_DIR", dir_b)
    PeerRegistry(peers_path()).add("peer_a", "127.0.0.1", 19101, id_a.public_key_hex)

    monkeypatch.setenv("SWARMKIT_RUNTIME_DIR", dir_a)
    pid_a = supervisor.start(concurrency=2, federation_host="127.0.0.1", federation_port=19101)

    monkeypatch.setenv("SWARMKIT_RUNTIME_DIR", dir_b)
    pid_b = supervisor.start(concurrency=2, federation_host="127.0.0.1", federation_port=19102)

    yield {"id_a": id_a, "id_b": id_b, "dir_a": dir_a, "dir_b": dir_b, "pid_a": pid_a, "pid_b": pid_b}

    monkeypatch.setenv("SWARMKIT_RUNTIME_DIR", dir_a)
    supervisor.stop()
    monkeypatch.setenv("SWARMKIT_RUNTIME_DIR", dir_b)
    supervisor.stop()
    shutil.rmtree(dir_a, ignore_errors=True)
    shutil.rmtree(dir_b, ignore_errors=True)


async def test_signed_task_completes_across_two_real_daemons(two_daemons):
    result = await send_signed_task(
        host="127.0.0.1",
        port=19102,
        identity=two_daemons["id_a"],
        from_peer="peer_a",
        cmd=["echo", "cross-daemon-proof"],
        jail_root=two_daemons["dir_b"],
        workdir=two_daemons["dir_b"],
        allowed_executables=["echo"],
    )
    assert result["exit_code"] == 0
    assert "cross-daemon-proof" in result["stdout"]
    assert result["pid"] not in (two_daemons["pid_a"], two_daemons["pid_b"]), (
        "task must run as its own subprocess, not report a daemon's own pid"
    )


async def test_forged_identity_is_rejected_by_the_real_daemon(two_daemons):
    with pytest.raises(RuntimeError, match="invalid signature"):
        await send_signed_task(
            host="127.0.0.1",
            port=19102,
            identity=two_daemons["id_b"],  # wrong key claiming to be peer_a
            from_peer="peer_a",
            cmd=["echo", "should-not-run"],
            jail_root=two_daemons["dir_b"],
            workdir=two_daemons["dir_b"],
            allowed_executables=["echo"],
        )


async def test_disallowed_command_is_blocked_pre_execution_by_the_real_daemon(two_daemons):
    with pytest.raises(RuntimeError):
        await send_signed_task(
            host="127.0.0.1",
            port=19102,
            identity=two_daemons["id_a"],
            from_peer="peer_a",
            cmd=["rm", "-rf", "/"],
            jail_root=two_daemons["dir_b"],
            workdir=two_daemons["dir_b"],
            allowed_executables=["echo"],
        )
