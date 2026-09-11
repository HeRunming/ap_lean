"""Cover the opt-in remote LeanProbe JSONL transport."""

from __future__ import annotations

import io
import json
import os
import threading
import time
from types import SimpleNamespace

import pytest

from leanflow_cli.formalization import remote_warm_probe as warm


class _Selector:
    def register(self, *_args):
        return None

    def select(self, _timeout):
        return [(object(), 1)]

    def close(self):
        return None


class _Process:
    pid = 1234

    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(
            json.dumps({"available": True, "success": True, "warmed": True})
            + "\n"
            + json.dumps({"available": True, "success": True, "has_errors": False, "output": ""})
            + "\n"
        )
        self.stderr = io.StringIO()

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


def test_warm_probe_is_opt_in(monkeypatch):
    monkeypatch.delenv(warm.WARM_PROBE_ENV, raising=False)
    assert warm.warm_probe_enabled() is False
    monkeypatch.setenv(warm.WARM_PROBE_ENV, "yes")
    assert warm.warm_probe_enabled() is True


def test_warmup_worker_count_is_small_and_bounded(monkeypatch):
    monkeypatch.setenv(warm.WARMUP_WORKERS_ENV, "99")
    assert warm.warmup_worker_count() == warm.WARMUP_WORKERS_MAX
    assert warm.warmup_worker_count(0) == 1


def test_warm_probe_reuses_one_remote_process_for_health_and_check(monkeypatch):
    assert warm._WARMUP_TIMEOUT_S == 3600.0
    process = _Process()
    popen_calls = []
    run_calls = []

    def fake_run(*args, **kwargs):
        run_calls.append((args, kwargs))
        if args[0][0] == "/usr/bin/ssh":
            return SimpleNamespace(returncode=0, stdout="/tmp/.leanflow-remote-warm-probe.abc123\n")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(warm.subprocess, "run", fake_run)
    monkeypatch.setattr(
        warm.subprocess, "Popen", lambda *args, **kwargs: popen_calls.append(args) or process
    )
    monkeypatch.setattr(warm.selectors, "DefaultSelector", _Selector)

    service = warm.RemoteWarmProbe(warm.LOCAL_PROJECT_ROOT)
    health = service.start()
    checked = service.check("import Mathlib\ntheorem demo : True := by sorry", 600)
    service.close(force=True)

    assert health["available"] is True
    assert checked["has_errors"] is False
    assert len(popen_calls) == 1
    assert len(run_calls) == 4
    service_sync = run_calls[2][0][0]
    assert str(warm.LOCAL_SERVICE_SCRIPT) in service_sync
    assert "/tmp/.leanflow-remote-warm-probe.abc123/lean-probe-service.py" in service_sync[-1]
    requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert [request["action"] for request in requests] == ["health", "check"]
    assert requests[1]["timeout_s"] == 600
    assert "/data/hrm/fate-x-work" in popen_calls[0][0][-1]
    assert "/tmp/.leanflow-remote-warm-probe.abc123/lean-probe-service.py" in popen_calls[0][0][-1]
    assert "trap 'rm -rf -- \"$service_dir\"' EXIT" in popen_calls[0][0][-1]
    assert f"export {warm.WARMUP_WORKERS_ENV}=1" in popen_calls[0][0][-1]
    assert "/usr/bin/timeout --signal=TERM --kill-after=1s 3600s" in popen_calls[0][0][-1]


def test_warm_probe_clamps_huge_candidate_timeout_before_sending(monkeypatch):
    process = _Process()
    popen_calls = []
    run_calls = []

    def fake_run(*args, **kwargs):
        run_calls.append((args, kwargs))
        if args[0][0] == "/usr/bin/ssh":
            return SimpleNamespace(returncode=0, stdout="/tmp/.leanflow-remote-warm-probe.abc123\n")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(warm.subprocess, "run", fake_run)
    monkeypatch.setattr(
        warm.subprocess, "Popen", lambda *args, **kwargs: popen_calls.append(args) or process
    )
    monkeypatch.setattr(warm.selectors, "DefaultSelector", _Selector)

    service = warm.RemoteWarmProbe(warm.LOCAL_PROJECT_ROOT)
    service.start()
    service.check("import Mathlib\ntheorem demo : True := by sorry", 9999)
    service.close(force=True)

    requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert requests[1]["timeout_s"] == 3600


def test_warm_probe_cleans_temp_service_when_launch_fails(monkeypatch):
    run_calls = []

    def fake_run(*args, **kwargs):
        run_calls.append((args, kwargs))
        if args[0][0] == "/usr/bin/ssh":
            return SimpleNamespace(
                returncode=0, stdout="/tmp/.leanflow-remote-warm-probe.fail123\n"
            )
        return SimpleNamespace(returncode=0, stdout="")

    def fail_popen(*_args, **_kwargs):
        raise OSError("ssh unavailable")

    monkeypatch.setattr(warm.subprocess, "run", fake_run)
    monkeypatch.setattr(warm.subprocess, "Popen", fail_popen)

    service = warm.RemoteWarmProbe(warm.LOCAL_PROJECT_ROOT)
    try:
        service.start()
    except OSError as exc:
        assert str(exc) == "ssh unavailable"
    else:
        raise AssertionError("start() should propagate launch failure")

    cleanup_command = run_calls[-1][0][0]
    assert cleanup_command[-1] == "rm -rf -- /tmp/.leanflow-remote-warm-probe.fail123"


def test_warm_probe_rejects_health_response_without_import_readiness(monkeypatch):
    """Do not use a resident REPL whose project imports are still cold."""
    process = _Process()
    process.stdout = io.StringIO(
        json.dumps(
            {
                "available": True,
                "warmed": False,
                "ready_for_checks": False,
                "hint": "LeanProbe import warmup did not complete",
            }
        )
        + "\n"
    )

    def fake_run(*args, **kwargs):
        if args[0][0] == "/usr/bin/ssh":
            return SimpleNamespace(
                returncode=0, stdout="/tmp/.leanflow-remote-warm-probe.cold123\n"
            )
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(warm.subprocess, "run", fake_run)
    monkeypatch.setattr(warm.subprocess, "Popen", lambda *_args, **_kwargs: process)
    service = warm.RemoteWarmProbe(warm.LOCAL_PROJECT_ROOT)
    with pytest.raises(RuntimeError, match="import warmup did not complete"):
        service.start()
    service.close(force=True)


def test_request_accumulates_fragmented_pipe_frames():
    read_fd, write_fd = os.pipe()
    stdout = os.fdopen(read_fd, "r", encoding="utf-8")

    process = SimpleNamespace(stdin=io.StringIO(), stderr=io.StringIO(), stdout=stdout)

    def write_fragments():
        os.write(write_fd, b'{"available": ')
        time.sleep(0.02)
        os.write(write_fd, b"true}\n")
        os.close(write_fd)

    writer = threading.Thread(target=write_fragments)
    writer.start()
    service = warm.RemoteWarmProbe(warm.LOCAL_PROJECT_ROOT)
    service.process = process
    try:
        assert service.request({"action": "health"}, 1) == {"available": True}
    finally:
        stdout.close()
        writer.join(timeout=1)


def test_invalid_json_diagnostic_contains_bounded_line_repr():
    process = _Process()
    process.stdout = io.StringIO("x" * 600 + "\n")
    service = warm.RemoteWarmProbe(warm.LOCAL_PROJECT_ROOT)
    service.process = process
    with pytest.raises(RuntimeError, match=r"invalid JSON line='x{512}…'"):
        service.request({"action": "health"}, 1)
