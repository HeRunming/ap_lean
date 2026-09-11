"""Run an opt-in persistent LeanProbe service on the HDP verification host.

The client deliberately owns only one bounded invocation.  It synchronizes the
authoritative checkout once, starts one remote Python process over SSH stdin,
and exchanges newline-delimited JSON requests until the caller closes it.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import selectors
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Mapping

REMOTE_HOST = "140.143.244.199"
REMOTE_USER = "hrm"
REMOTE_PORT = 49322
REMOTE_PROJECT_ROOT = Path("/data/hrm/fate-x-work")
LOCAL_PROJECT_ROOT = Path("/Users/blackbox/m2f/fate-x-work")
LOCAL_SERVICE_SCRIPT = Path(__file__).resolve().parents[2] / "remote-bin" / "lean-probe-service.py"
WARM_PROBE_ENV = "LEANFLOW_REMOTE_WARM_PROBE"
WARMUP_WORKERS_ENV = "LEANFLOW_REMOTE_WARMUP_WORKERS"
WARMUP_WORKERS_DEFAULT = 1
WARMUP_WORKERS_MAX = 4
REMOTE_LEAN_TIMEOUT_MAX_S = 3600.0
_REMOTE_SERVICE_DIR_PREFIX = "/tmp/.leanflow-remote-warm-probe."
_REMOTE_SERVICE_SCRIPT_NAME = "lean-probe-service.py"
# Match the service's bounded import readiness probe and the remote watchdog's
# safe maximum. Candidate verification keeps its independent hard 120-second
# timeout.
_WARMUP_TIMEOUT_S = 3600.0
_SSH_OPTIONS = (
    "-p",
    str(REMOTE_PORT),
    "-o",
    "BatchMode=yes",
    "-o",
    "ControlMaster=no",
    "-o",
    "ControlPath=none",
)


def warm_probe_enabled(value: bool = False) -> bool:
    """Return whether the per-invocation remote warm service was requested."""
    if value:
        return True
    return os.environ.get(WARM_PROBE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def warmup_worker_count(value: int | None = None) -> int:
    """Return a small bounded remote LeanProbe session capacity."""
    if value is None:
        raw = os.environ.get(WARMUP_WORKERS_ENV, "")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = WARMUP_WORKERS_DEFAULT
    return max(1, min(int(value), WARMUP_WORKERS_MAX))


def _sync_command(project_root: Path) -> list[str]:
    """Build the one-shot source synchronization command for the remote checkout."""
    ssh = "/usr/bin/ssh " + " ".join(_SSH_OPTIONS)
    return [
        "/usr/bin/rsync",
        "-az",
        f"--exclude-from={Path(__file__).resolve().parents[2] / 'core' / 'remote-project-sync.exclude'}",
        "-e",
        ssh,
        f"{project_root}/",
        f"{REMOTE_USER}@{REMOTE_HOST}:{REMOTE_PROJECT_ROOT}/",
    ]


def _mktemp_command() -> list[str]:
    """Build the SSH command that allocates an isolated per-invocation directory."""
    return [
        "/usr/bin/ssh",
        *_SSH_OPTIONS,
        f"{REMOTE_USER}@{REMOTE_HOST}",
        f"mktemp -d {_REMOTE_SERVICE_DIR_PREFIX}XXXXXX",
    ]


def _service_sync_command(service_dir: str) -> list[str]:
    """Build the one-shot rsync command for the temporary remote service script."""
    ssh = "/usr/bin/ssh " + " ".join(_SSH_OPTIONS)
    return [
        "/usr/bin/rsync",
        "-az",
        "-e",
        ssh,
        str(LOCAL_SERVICE_SCRIPT),
        f"{REMOTE_USER}@{REMOTE_HOST}:{service_dir}/{_REMOTE_SERVICE_SCRIPT_NAME}",
    ]


class RemoteWarmProbe:
    """Own one remote LeanProbe process and serialize bounded JSONL checks."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        startup_timeout_s: float = 15.0,
        warmup_workers: int | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.startup_timeout_s = max(1.0, float(startup_timeout_s))
        self.warmup_workers = warmup_worker_count(warmup_workers)
        self.process: subprocess.Popen[str] | None = None
        self._remote_service_dir: str | None = None
        self._io_lock = threading.Lock()
        self._stdout_buffer = bytearray()

    def start(self) -> Mapping[str, Any]:
        """Synchronize HDP and start the service only after bounded import readiness."""
        if self.project_root != LOCAL_PROJECT_ROOT:
            raise RuntimeError(
                f"warm remote probe requires the HDP checkout at {LOCAL_PROJECT_ROOT}"
            )
        subprocess.run(
            _sync_command(self.project_root),
            check=True,
            capture_output=True,
            text=True,
            timeout=self.startup_timeout_s,
        )
        temp_result = subprocess.run(
            _mktemp_command(),
            check=True,
            capture_output=True,
            text=True,
            timeout=self.startup_timeout_s,
        )
        service_dir = (temp_result.stdout or "").strip()
        suffix = service_dir[len(_REMOTE_SERVICE_DIR_PREFIX) :]
        if not service_dir.startswith(_REMOTE_SERVICE_DIR_PREFIX) or not suffix or "/" in suffix:
            if service_dir.startswith(_REMOTE_SERVICE_DIR_PREFIX):
                self._remote_service_dir = service_dir
                self._cleanup_remote_service()
            raise RuntimeError("remote warm probe returned an invalid temporary service path")
        self._remote_service_dir = service_dir
        try:
            subprocess.run(
                _service_sync_command(service_dir),
                check=True,
                capture_output=True,
                text=True,
                timeout=self.startup_timeout_s,
            )
        except Exception:
            self._cleanup_remote_service()
            raise
        service_script = f"{service_dir}/{_REMOTE_SERVICE_SCRIPT_NAME}"
        quoted_service_dir = shlex.quote(service_dir)
        quoted_service_script = shlex.quote(service_script)
        command = (
            f"service_dir={quoted_service_dir}; "
            f"export {WARMUP_WORKERS_ENV}={self.warmup_workers}; "
            "trap 'rm -rf -- \"$service_dir\"' EXIT; "
            f"cd {REMOTE_PROJECT_ROOT} && "
            "if [ ! -x /usr/bin/timeout ]; then exit 127; fi; "
            "/usr/bin/timeout --signal=TERM --kill-after=1s 3600s "
            "python3 -u "
            f"{quoted_service_script}"
        )
        try:
            self.process = subprocess.Popen(
                ["/usr/bin/ssh", *_SSH_OPTIONS, f"{REMOTE_USER}@{REMOTE_HOST}", command],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=(os.name == "posix"),
                bufsize=1,
            )
        except Exception:
            self._cleanup_remote_service()
            raise
        # A cold Mathlib import can take several minutes, so do not report a
        # merely started REPL as ready. Candidate checks remain independently
        # bounded by their 120-second timeout.
        response = self.request(
            {"action": "health", "warm": True},
            max(self.startup_timeout_s, _WARMUP_TIMEOUT_S + 2.0),
        )
        if not bool(response.get("available")):
            raise RuntimeError(
                str(response.get("error") or response.get("hint") or "remote LeanProbe unavailable")
            )
        if response.get("ready_for_checks") is False or response.get("warmed") is False:
            raise RuntimeError(
                str(
                    response.get("error")
                    or response.get("hint")
                    or "remote LeanProbe import warmup did not complete"
                )
            )
        return response

    def request(self, payload: Mapping[str, Any], timeout_s: float) -> dict[str, Any]:
        """Send one JSON request and read exactly one bounded JSON response."""
        process = self.process
        if process is None or process.stdin is None or process.stdout is None:
            raise RuntimeError("remote warm probe is not started")
        with self._io_lock:
            process.stdin.write(json.dumps(dict(payload), ensure_ascii=False) + "\n")
            process.stdin.flush()
            line = self._read_response_line(process, timeout_s)
            if line is None:
                self.close(force=True)
                raise TimeoutError(f"remote warm probe timed out after {timeout_s:g} seconds")
        if not line:
            stderr = ""
            if process.stderr is not None:
                with contextlib.suppress(Exception):
                    stderr = process.stderr.read()[-2000:]
            raise RuntimeError(
                f"remote warm probe exited unexpectedly{': ' + stderr if stderr else ''}"
            )
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            bounded = repr(line[:512] + ("…" if len(line) > 512 else ""))
            raise RuntimeError(f"remote warm probe returned invalid JSON line={bounded}") from exc
        if not isinstance(response, dict):
            raise RuntimeError("remote warm probe returned a non-object response")
        return response

    def _read_response_line(self, process: subprocess.Popen[str], timeout_s: float) -> str | None:
        """Read one complete JSONL line without blocking past the request deadline.

        Real subprocess streams are read from their file descriptor so selector
        readiness handles fragmented SSH packets correctly.  Lightweight text
        streams used by tests retain the simpler ``readline`` fallback.
        """
        stream = process.stdout
        if stream is None:
            return ""
        try:
            fd = stream.fileno()
        except (AttributeError, OSError, ValueError):
            selector = selectors.DefaultSelector()
            try:
                try:
                    selector.register(stream, selectors.EVENT_READ)
                except (OSError, ValueError):
                    return stream.readline()
                if not selector.select(max(0.001, float(timeout_s))):
                    return None
                return stream.readline()
            finally:
                selector.close()

        deadline = time.monotonic() + max(0.001, float(timeout_s))
        selector = selectors.DefaultSelector()
        try:
            selector.register(fd, selectors.EVENT_READ)
            while True:
                newline = self._stdout_buffer.find(b"\n")
                if newline >= 0:
                    line = bytes(self._stdout_buffer[:newline])
                    del self._stdout_buffer[: newline + 1]
                    return line.decode("utf-8", errors="replace") + "\n"
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                if not selector.select(remaining):
                    return None
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    if not self._stdout_buffer:
                        return ""
                    line = bytes(self._stdout_buffer)
                    self._stdout_buffer.clear()
                    return line.decode("utf-8", errors="replace")
                self._stdout_buffer.extend(chunk)
        finally:
            selector.close()

    def check(self, code: str, timeout_s: float) -> dict[str, Any]:
        """Check one candidate remotely, preserving Lean diagnostics as JSON data."""
        bounded_timeout = max(1, min(int(timeout_s), int(REMOTE_LEAN_TIMEOUT_MAX_S)))
        return self.request(
            {"action": "check", "code": code, "timeout_s": bounded_timeout},
            bounded_timeout + 2.0,
        )

    def close(self, *, force: bool = False) -> None:
        """Close the service and kill its SSH process group if cleanup is needed."""
        process, self.process = self.process, None
        if process is None:
            self._cleanup_remote_service()
            return
        if not force and process.poll() is None and process.stdin is not None:
            with contextlib.suppress(Exception):
                process.stdin.write('{"action":"close"}\n')
                process.stdin.flush()
                process.wait(timeout=2.0)
        if process.poll() is None:
            if os.name == "posix":
                with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(process.pid, signal.SIGTERM)
            else:
                with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                    process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                process.wait(timeout=1.0)
        if process.poll() is None:
            if os.name == "posix":
                with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(process.pid, signal.SIGKILL)
            else:
                with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                    process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            process.wait(timeout=1.0)
        self._cleanup_remote_service()

    def _cleanup_remote_service(self) -> None:
        """Best-effort remove the per-invocation remote service directory."""
        service_dir, self._remote_service_dir = self._remote_service_dir, None
        if not service_dir:
            return
        with contextlib.suppress(Exception):
            subprocess.run(
                [
                    "/usr/bin/ssh",
                    *_SSH_OPTIONS,
                    f"{REMOTE_USER}@{REMOTE_HOST}",
                    f"rm -rf -- {shlex.quote(service_dir)}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=min(5.0, self.startup_timeout_s),
            )

    def __enter__(self) -> RemoteWarmProbe:
        """Start the service for a bounded invocation."""
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        """Always tear down the remote process and its SSH transport."""
        self.close()

    def __del__(self) -> None:
        """Best-effort reap if an action aborts before its normal return path."""
        with contextlib.suppress(Exception):
            self.close(force=True)


def main(argv: list[str] | None = None) -> int:
    """Run a documented remote warm-service health probe without a campaign."""
    parser = argparse.ArgumentParser(description="Probe the HDP remote LeanProbe warm service")
    parser.add_argument(
        "--health", action="store_true", help="start the service and print health JSON"
    )
    parser.add_argument("--project-root", default=str(LOCAL_PROJECT_ROOT))
    parser.add_argument(
        "--warmup-workers",
        type=int,
        default=None,
        help=f"remote LeanProbe session capacity (1-{WARMUP_WORKERS_MAX})",
    )
    args = parser.parse_args(argv)
    if not args.health:
        parser.error("--health is required")
    if args.warmup_workers is not None and not 1 <= args.warmup_workers <= WARMUP_WORKERS_MAX:
        parser.error(f"--warmup-workers must be between 1 and {WARMUP_WORKERS_MAX}")
    service = RemoteWarmProbe(args.project_root, warmup_workers=args.warmup_workers)
    try:
        print(json.dumps(service.start(), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({"available": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
