"""Proves the PyO3 boundary isn't the theater: calling swarmkit._native directly
(no Python fallback in between) actually forks a real OS process."""

from __future__ import annotations

import pytest

from swarmkit import _native


async def test_run_sandboxed_returns_real_pid_and_output(tmp_path):
    result = await _native.run_sandboxed(
        cmd=["echo", "hello-from-python"],
        jail_root=str(tmp_path),
        workdir=str(tmp_path),
        allowed_executables=["echo"],
        timeout_secs=5.0,
    )
    assert result["pid"] > 0
    assert result["exit_code"] == 0
    assert "hello-from-python" in result["stdout"]
    assert not result["timed_out"]


async def test_non_allowlisted_command_is_rejected(tmp_path):
    with pytest.raises(RuntimeError):
        await _native.run_sandboxed(
            cmd=["rm", "-rf", "/"],
            jail_root=str(tmp_path),
            workdir=str(tmp_path),
            allowed_executables=["echo"],
        )


async def test_workdir_outside_jail_root_is_rejected(tmp_path):
    jail = tmp_path / "jail"
    outside = tmp_path / "outside"
    jail.mkdir()
    outside.mkdir()
    with pytest.raises(RuntimeError):
        await _native.run_sandboxed(
            cmd=["echo", "hi"],
            jail_root=str(jail),
            workdir=str(outside),
            allowed_executables=["echo"],
        )


async def test_slow_command_is_killed_on_timeout(tmp_path):
    result = await _native.run_sandboxed(
        cmd=["sleep", "5"],
        jail_root=str(tmp_path),
        workdir=str(tmp_path),
        allowed_executables=["sleep"],
        timeout_secs=0.2,
    )
    assert result["timed_out"]
    assert result["pid"] > 0
