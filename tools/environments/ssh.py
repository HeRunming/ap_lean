"""SSH remote execution environment with ControlMaster connection persistence."""

import contextlib
import hashlib
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from tools.environments.base import BaseEnvironment
from tools.environments.persistent_shell import PersistentShellMixin
from tools.utilities.interrupt import is_interrupted

logger = logging.getLogger(__name__)


def _ensure_ssh_available() -> None:
    """Fail fast with a clear error when the SSH client is unavailable."""
    if not shutil.which("ssh"):
        raise RuntimeError(
            "SSH is not installed or not in PATH. Install OpenSSH client: apt install openssh-client"
        )


class SSHEnvironment(PersistentShellMixin, BaseEnvironment):
    """Run commands on a remote machine over SSH.

    Uses SSH ControlMaster for connection persistence so subsequent
    commands are fast. Security benefit: the agent cannot modify its
    own code since execution happens on a separate machine.

    Foreground commands are interruptible: the local ssh process is killed
    and a remote kill is attempted over the ControlMaster socket.

    When ``persistent=True``, a single long-lived bash shell is kept alive
    over SSH and state (cwd, env vars, shell variables) persists across
    ``execute()`` calls.  Output capture uses file-based IPC on the remote
    host (stdout/stderr/exit-code written to temp files, polled via fast
    ControlMaster one-shot reads).
    """

    def __init__(
        self,
        host: str,
        user: str,
        cwd: str = "~",
        timeout: int = 60,
        port: int = 22,
        key_path: str = "",
        persistent: bool = False,
    ):
        super().__init__(cwd=cwd, timeout=timeout)
        self.host = host
        self.user = user
        self.port = port
        self.key_path = key_path
        self.persistent = persistent
        self.remote_root = cwd

        socket_root = Path("/tmp") if Path("/tmp").is_dir() else Path(tempfile.gettempdir())
        local_uid = getattr(os, "getuid", lambda: 0)()
        self.control_dir = socket_root / f"leanflow-ssh-{local_uid}"
        self.control_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.control_dir.chmod(0o700)
        # macOS temp roots can already consume most of the Unix-domain socket
        # path limit. Hash the endpoint instead of embedding it verbatim so
        # ControlMaster works consistently across local platforms.
        endpoint = f"{user}@{host}:{port}"
        endpoint_id = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:20]
        self.control_socket = self.control_dir / f"ssh-{endpoint_id}.sock"
        _ensure_ssh_available()
        self._establish_connection()

        if self.persistent:
            self._init_persistent_shell()

    def _build_ssh_command(self, extra_args: list | None = None) -> list:
        cmd = ["ssh"]
        cmd.extend(["-o", f"ControlPath={self.control_socket}"])
        cmd.extend(["-o", "ControlMaster=auto"])
        cmd.extend(["-o", "ControlPersist=300"])
        cmd.extend(["-o", "BatchMode=yes"])
        cmd.extend(["-o", "StrictHostKeyChecking=accept-new"])
        cmd.extend(["-o", "ConnectTimeout=10"])
        if self.port != 22:
            cmd.extend(["-p", str(self.port)])
        if self.key_path:
            cmd.extend(["-i", self.key_path])
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(f"{self.user}@{self.host}")
        return cmd

    def _map_host_project_paths(self, command: str) -> str:
        """Map workflow-local absolute project paths into the remote checkout."""
        host_root = str(os.getenv("LEANFLOW_PROJECT_ROOT", "") or "").rstrip("/")
        remote_root = str(self.remote_root or "").rstrip("/")
        if not host_root or not remote_root or host_root == remote_root:
            return command
        mapped = command.replace(shlex.quote(host_root), shlex.quote(remote_root))
        return mapped.replace(host_root, remote_root)

    def _map_host_project_path(self, path: str) -> str:
        """Map one cwd/file path rooted in the local workflow checkout."""
        value = str(path or "")
        host_root = str(os.getenv("LEANFLOW_PROJECT_ROOT", "") or "").rstrip("/")
        remote_root = str(self.remote_root or "").rstrip("/")
        if not host_root or not remote_root:
            return value
        if value == host_root:
            return remote_root
        if value.startswith(host_root + "/"):
            return remote_root + value[len(host_root) :]
        return value

    def _map_remote_project_paths(self, output: str) -> str:
        """Present remote checkout paths using the local workflow identity."""
        host_root = str(os.getenv("LEANFLOW_PROJECT_ROOT", "") or "").rstrip("/")
        remote_root = str(self.remote_root or "").rstrip("/")
        if not host_root or not remote_root or host_root == remote_root:
            return output
        return str(output or "").replace(remote_root, host_root)

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> dict:
        sync_error = self._sync_project_to_remote()
        if sync_error:
            return {"output": sync_error, "returncode": 1}
        result = super().execute(command, cwd, timeout=timeout, stdin_data=stdin_data)
        result["output"] = self._map_remote_project_paths(str(result.get("output", "") or ""))
        return result

    def _sync_project_to_remote(self) -> str:
        """Synchronize local project sources before remote verification commands."""
        enabled = str(os.getenv("TERMINAL_SSH_SYNC_PROJECT", "") or "").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return ""
        host_root = Path(str(os.getenv("LEANFLOW_PROJECT_ROOT", "") or "")).resolve()
        remote_root = str(self.remote_root or "").rstrip("/")
        if not host_root.is_dir() or not remote_root:
            return "SSH project sync requires valid local and remote project roots"
        if not shutil.which("rsync"):
            return "SSH project sync requires rsync on the local host"
        ssh_parts = ["ssh", "-o", f"ControlPath={self.control_socket}"]
        if self.port != 22:
            ssh_parts.extend(["-p", str(self.port)])
        if self.key_path:
            ssh_parts.extend(["-i", self.key_path])
        command = [
            "rsync",
            "-az",
            "--exclude=.git",
            "--exclude=.git/",
            "--exclude=.lake",
            "--exclude=.lake/",
            "--exclude=.leanflow",
            "--exclude=.leanflow/",
            "--exclude=.venv",
            "--exclude=.venv/",
            "-e",
            shlex.join(ssh_parts),
            f"{host_root}/",
            f"{self.user}@{self.host}:{remote_root}/",
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"SSH project sync failed before verification: {exc}"
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            return f"SSH project sync failed before verification: {detail}"
        return ""

    def _establish_connection(self):
        """Establish the shared connection, retrying transient banner/socket failures."""
        try:
            attempts = max(1, int(os.getenv("TERMINAL_SSH_CONNECT_ATTEMPTS", "3")))
        except ValueError:
            attempts = 3
        last_error = ""
        last_timeout: subprocess.TimeoutExpired | None = None
        for attempt in range(1, attempts + 1):
            cmd = self._build_ssh_command()
            cmd.append("echo 'SSH connection established'")
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                if result.returncode == 0:
                    return
                last_error = result.stderr.strip() or result.stdout.strip()
                last_timeout = None
            except subprocess.TimeoutExpired as exc:
                last_timeout = exc
                last_error = f"SSH connection to {self.user}@{self.host} timed out"
            if attempt < attempts:
                # A dead ControlMaster socket can otherwise poison every probe in
                # the action. Remove only this endpoint's socket before retrying.
                with contextlib.suppress(OSError):
                    self.control_socket.unlink()
                time.sleep(min(2.0, 0.5 * attempt))
        if last_timeout is not None:
            raise RuntimeError(last_error) from last_timeout
        raise RuntimeError(f"SSH connection failed after {attempts} attempts: {last_error}")

    _poll_interval: float = 0.15

    @property
    def _temp_prefix(self) -> str:
        return f"/tmp/leanflow-ssh-{self._session_id}"

    def _spawn_shell_process(self) -> subprocess.Popen:
        cmd = self._build_ssh_command()
        cmd.append("bash -l")
        return subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )

    def _read_temp_files(self, *paths: str) -> list[str]:
        if len(paths) == 1:
            cmd = self._build_ssh_command()
            cmd.append(f"cat {paths[0]} 2>/dev/null")
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                return [result.stdout]
            except (subprocess.TimeoutExpired, OSError):
                return [""]

        delim = f"__LEANFLOW_SEP_{self._session_id}__"
        script = "; ".join(f"cat {p} 2>/dev/null; echo '{delim}'" for p in paths)
        cmd = self._build_ssh_command()
        cmd.append(script)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            parts = result.stdout.split(delim + "\n")
            return [parts[i] if i < len(parts) else "" for i in range(len(paths))]
        except (subprocess.TimeoutExpired, OSError):
            return [""] * len(paths)

    def _kill_shell_children(self):
        if self._shell_pid is None:
            return
        cmd = self._build_ssh_command()
        cmd.append(f"pkill -P {self._shell_pid} 2>/dev/null; true")
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            subprocess.run(cmd, capture_output=True, timeout=5)

    def _cleanup_temp_files(self):
        cmd = self._build_ssh_command()
        cmd.append(f"rm -f {self._temp_prefix}-*")
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            subprocess.run(cmd, capture_output=True, timeout=5)

    def _execute_oneshot(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> dict:
        """Execute a single SSH command, streaming output via background reader thread and handling interruption/timeout. Returns dict with combined stdout/stderr and exit code; returns 130 if interrupted, or calls _timeout_result() if the effective timeout is exceeded."""
        work_dir = self._map_host_project_path(cwd or self.cwd)
        exec_command, sudo_stdin = self._prepare_command(self._map_host_project_paths(command))
        wrapped = f"cd {work_dir} && {exec_command}"
        effective_timeout = timeout or self.timeout

        if sudo_stdin is not None and stdin_data is not None:
            effective_stdin = sudo_stdin + stdin_data
        elif sudo_stdin is not None:
            effective_stdin = sudo_stdin
        else:
            effective_stdin = stdin_data

        cmd = self._build_ssh_command()
        cmd.append(wrapped)

        kwargs = self._build_run_kwargs(timeout, effective_stdin)
        kwargs.pop("timeout", None)
        _output_chunks = []
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if effective_stdin else subprocess.DEVNULL,
            text=True,
        )

        if effective_stdin:
            try:
                proc.stdin.write(effective_stdin)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        def _drain():
            try:
                for line in proc.stdout:
                    _output_chunks.append(line)
            except Exception:
                pass

        reader = threading.Thread(target=_drain, daemon=True)
        reader.start()
        deadline = time.monotonic() + effective_timeout

        while proc.poll() is None:
            if is_interrupted():
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                reader.join(timeout=2)
                return {
                    "output": "".join(_output_chunks) + "\n[Command interrupted]",
                    "returncode": 130,
                }
            if time.monotonic() > deadline:
                proc.kill()
                reader.join(timeout=2)
                return self._timeout_result(effective_timeout)
            time.sleep(0.2)

        reader.join(timeout=5)
        return {"output": "".join(_output_chunks), "returncode": proc.returncode}

    def cleanup(self):
        super().cleanup()
        if self.control_socket.exists():
            try:
                cmd = [
                    "ssh",
                    "-o",
                    f"ControlPath={self.control_socket}",
                    "-O",
                    "exit",
                    f"{self.user}@{self.host}",
                ]
                subprocess.run(cmd, capture_output=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
            with contextlib.suppress(OSError):
                self.control_socket.unlink()
