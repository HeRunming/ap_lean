"""Tests for the SSH remote execution environment backend."""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools.environments import ssh as ssh_env
from tools.environments.ssh import SSHEnvironment


def test_control_socket_uses_short_private_endpoint_hash(monkeypatch):
    monkeypatch.setattr(SSHEnvironment, "_establish_connection", lambda self: None)
    monkeypatch.setattr("tools.environments.ssh.os.getuid", lambda: 1234)

    environment = SSHEnvironment(
        "host-with-a-deliberately-long-name.example.com",
        "user-with-a-deliberately-long-name",
        port=49322,
    )

    assert environment.control_socket.parent == Path("/tmp/leanflow-ssh-1234")
    assert environment.control_socket.parent.stat().st_mode & 0o777 == 0o700
    assert environment.control_socket.name.startswith("ssh-")
    assert len(environment.control_socket.name) == len("ssh-") + 20 + len(".sock")


_SSH_HOST = os.getenv("TERMINAL_SSH_HOST", "")
_SSH_USER = os.getenv("TERMINAL_SSH_USER", "")
_SSH_PORT = int(os.getenv("TERMINAL_SSH_PORT", "22"))
_SSH_KEY = os.getenv("TERMINAL_SSH_KEY", "")

_has_ssh = bool(_SSH_HOST and _SSH_USER)

requires_ssh = pytest.mark.skipif(
    not _has_ssh,
    reason="TERMINAL_SSH_HOST / TERMINAL_SSH_USER not set",
)


def _run(command, task_id="ssh_test", **kwargs):
    from tools.implementations.terminal_tool import terminal_tool

    return json.loads(terminal_tool(command, task_id=task_id, **kwargs))


def _cleanup(task_id="ssh_test"):
    from tools.implementations.terminal_tool import cleanup_vm

    cleanup_vm(task_id)


class TestBuildSSHCommand:
    @pytest.fixture(autouse=True)
    def _mock_connection(self, monkeypatch):
        monkeypatch.setattr(
            "tools.environments.ssh.subprocess.run",
            lambda *a, **k: subprocess.CompletedProcess([], 0),
        )
        monkeypatch.setattr(
            "tools.environments.ssh.subprocess.Popen",
            lambda *a, **k: MagicMock(stdout=iter([]), stderr=iter([]), stdin=MagicMock()),
        )
        monkeypatch.setattr("tools.environments.ssh.time.sleep", lambda _: None)

    def test_base_flags(self):
        env = SSHEnvironment(host="h", user="u")
        cmd = " ".join(env._build_ssh_command())
        for flag in (
            "ControlMaster=auto",
            "ControlPersist=300",
            "BatchMode=yes",
            "StrictHostKeyChecking=accept-new",
        ):
            assert flag in cmd

    def test_custom_port(self):
        env = SSHEnvironment(host="h", user="u", port=2222)
        cmd = env._build_ssh_command()
        assert "-p" in cmd and "2222" in cmd

    def test_key_path(self):
        env = SSHEnvironment(host="h", user="u", key_path="/k")
        cmd = env._build_ssh_command()
        assert "-i" in cmd and "/k" in cmd

    def test_user_host_suffix(self):
        env = SSHEnvironment(host="h", user="u")
        assert env._build_ssh_command()[-1] == "u@h"


class TestTerminalToolConfig:
    def test_ssh_persistent_default_true(self, monkeypatch):
        """SSH persistent defaults to True (via TERMINAL_PERSISTENT_SHELL)."""
        monkeypatch.delenv("TERMINAL_SSH_PERSISTENT", raising=False)
        monkeypatch.delenv("TERMINAL_PERSISTENT_SHELL", raising=False)
        from tools.implementations.terminal_tool import _get_env_config

        assert _get_env_config()["ssh_persistent"] is True

    def test_ssh_persistent_explicit_false(self, monkeypatch):
        """Per-backend env var overrides the global default."""
        monkeypatch.setenv("TERMINAL_SSH_PERSISTENT", "false")
        from tools.implementations.terminal_tool import _get_env_config

        assert _get_env_config()["ssh_persistent"] is False

    def test_ssh_persistent_explicit_true(self, monkeypatch):
        monkeypatch.setenv("TERMINAL_SSH_PERSISTENT", "true")
        from tools.implementations.terminal_tool import _get_env_config

        assert _get_env_config()["ssh_persistent"] is True

    def test_ssh_persistent_respects_config(self, monkeypatch):
        """TERMINAL_PERSISTENT_SHELL=false disables SSH persistent by default."""
        monkeypatch.delenv("TERMINAL_SSH_PERSISTENT", raising=False)
        monkeypatch.setenv("TERMINAL_PERSISTENT_SHELL", "false")
        from tools.implementations.terminal_tool import _get_env_config

        assert _get_env_config()["ssh_persistent"] is False


class TestSSHPreflight:
    def test_ensure_ssh_available_raises_clear_error_when_missing(self, monkeypatch):
        monkeypatch.setattr(ssh_env.shutil, "which", lambda _name: None)

        with pytest.raises(RuntimeError, match="SSH is not installed or not in PATH"):
            ssh_env._ensure_ssh_available()

    def test_ssh_environment_checks_availability_before_connect(self, monkeypatch):
        monkeypatch.setattr(ssh_env.shutil, "which", lambda _name: None)
        monkeypatch.setattr(
            ssh_env.SSHEnvironment,
            "_establish_connection",
            lambda self: pytest.fail("_establish_connection should not run when ssh is missing"),
        )

        with pytest.raises(RuntimeError, match="openssh-client"):
            ssh_env.SSHEnvironment(host="example.com", user="alice")

    def test_ssh_environment_connects_when_ssh_exists(self, monkeypatch):
        called = {"count": 0}

        monkeypatch.setattr(ssh_env.shutil, "which", lambda _name: "/usr/bin/ssh")

        def _fake_establish(self):
            called["count"] += 1

        monkeypatch.setattr(ssh_env.SSHEnvironment, "_establish_connection", _fake_establish)

        env = ssh_env.SSHEnvironment(host="example.com", user="alice")

        assert called["count"] == 1
        assert env.host == "example.com"
        assert env.user == "alice"

    def test_connection_retries_transient_failure_and_clears_socket(self, monkeypatch):
        calls = []
        sleeps = []
        monkeypatch.setenv("TERMINAL_SSH_CONNECT_ATTEMPTS", "3")
        monkeypatch.setattr(ssh_env.shutil, "which", lambda _name: "/usr/bin/ssh")
        monkeypatch.setattr(ssh_env.time, "sleep", sleeps.append)

        def run(*args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(
                args[0], 255 if len(calls) == 1 else 0, "", "connection closed"
            )

        monkeypatch.setattr(ssh_env.subprocess, "run", run)

        env = ssh_env.SSHEnvironment(host="example.com", user="alice")

        assert len(calls) == 2
        assert sleeps == [0.5]
        assert not env.control_socket.exists()

    def test_connection_reports_exhausted_timeout_retries(self, monkeypatch):
        monkeypatch.setenv("TERMINAL_SSH_CONNECT_ATTEMPTS", "2")
        monkeypatch.setattr(ssh_env.shutil, "which", lambda _name: "/usr/bin/ssh")
        monkeypatch.setattr(ssh_env.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(
            ssh_env.subprocess,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(args[0], kwargs["timeout"])
            ),
        )

        with pytest.raises(RuntimeError, match="timed out"):
            ssh_env.SSHEnvironment(host="example.com", user="alice")


def test_project_sync_uses_local_checkout_as_remote_verification_authority(monkeypatch, tmp_path):
    monkeypatch.setattr(SSHEnvironment, "_establish_connection", lambda self: None)
    monkeypatch.setenv("TERMINAL_SSH_SYNC_PROJECT", "true")
    monkeypatch.setenv("LEANFLOW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(ssh_env.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(ssh_env.subprocess, "run", run)
    environment = SSHEnvironment(
        host="example.com",
        user="alice",
        cwd="/srv/lean/project",
        port=2222,
        key_path="/keys/lean",
    )

    assert environment._sync_project_to_remote() == ""
    command = calls[-1][0]
    assert command[:2] == ["rsync", "-az"]
    assert "--exclude=.lake/" in command
    assert command[-2] == f"{tmp_path}/"
    assert command[-1] == "alice@example.com:/srv/lean/project/"


def test_project_sync_failure_blocks_stale_remote_verification(monkeypatch, tmp_path):
    monkeypatch.setattr(SSHEnvironment, "_establish_connection", lambda self: None)
    monkeypatch.setenv("TERMINAL_SSH_SYNC_PROJECT", "true")
    monkeypatch.setenv("LEANFLOW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(ssh_env.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        ssh_env.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 23, "", "sync failed"),
    )
    environment = SSHEnvironment(host="example.com", user="alice", cwd="/srv/lean/project")

    result = environment.execute("lake env lean Main.lean")

    assert result["returncode"] == 1
    assert "sync failed before verification" in result["output"]


def _setup_ssh_env(monkeypatch, persistent: bool):
    monkeypatch.setenv("TERMINAL_ENV", "ssh")
    monkeypatch.setenv("TERMINAL_SSH_HOST", _SSH_HOST)
    monkeypatch.setenv("TERMINAL_SSH_USER", _SSH_USER)
    monkeypatch.setenv("TERMINAL_SSH_PERSISTENT", "true" if persistent else "false")
    if _SSH_PORT != 22:
        monkeypatch.setenv("TERMINAL_SSH_PORT", str(_SSH_PORT))
    if _SSH_KEY:
        monkeypatch.setenv("TERMINAL_SSH_KEY", _SSH_KEY)


@requires_ssh
class TestOneShotSSH:
    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        _setup_ssh_env(monkeypatch, persistent=False)
        yield
        _cleanup()

    def test_echo(self):
        r = _run("echo hello")
        assert r["exit_code"] == 0
        assert "hello" in r["output"]

    def test_exit_code(self):
        r = _run("exit 42")
        assert r["exit_code"] == 42

    def test_state_does_not_persist(self):
        _run("export LEANFLOW_ONESHOT_TEST=yes")
        r = _run("echo $LEANFLOW_ONESHOT_TEST")
        assert r["output"].strip() == ""


@requires_ssh
class TestPersistentSSH:
    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        _setup_ssh_env(monkeypatch, persistent=True)
        yield
        _cleanup()

    def test_echo(self):
        r = _run("echo hello-persistent")
        assert r["exit_code"] == 0
        assert "hello-persistent" in r["output"]

    def test_env_var_persists(self):
        _run("export LEANFLOW_PERSIST_TEST=works")
        r = _run("echo $LEANFLOW_PERSIST_TEST")
        assert r["output"].strip() == "works"

    def test_cwd_persists(self):
        _run("cd /tmp")
        r = _run("pwd")
        assert r["output"].strip() == "/tmp"

    def test_exit_code(self):
        r = _run("(exit 42)")
        assert r["exit_code"] == 42

    def test_stderr(self):
        r = _run("echo oops >&2")
        assert r["exit_code"] == 0
        assert "oops" in r["output"]

    def test_multiline_output(self):
        r = _run("echo a; echo b; echo c")
        lines = r["output"].strip().splitlines()
        assert lines == ["a", "b", "c"]

    def test_timeout_then_recovery(self):
        r = _run("sleep 999", timeout=2)
        assert r["exit_code"] == 124
        r = _run("echo alive")
        assert r["exit_code"] == 0
        assert "alive" in r["output"]

    def test_large_output(self):
        r = _run("seq 1 1000")
        assert r["exit_code"] == 0
        lines = r["output"].strip().splitlines()
        assert len(lines) == 1000
        assert lines[0] == "1"
        assert lines[-1] == "1000"
