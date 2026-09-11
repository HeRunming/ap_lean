"""Cover the remote LeanProbe service's truthful warm-readiness contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_service():
    """Load the hyphenated remote service module for focused unit tests."""
    path = Path(__file__).parents[2] / "remote-bin" / "lean-probe-service.py"
    spec = importlib.util.spec_from_file_location("lean_probe_service", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load remote LeanProbe service")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Probe:
    """Return deterministic capability and import-warmup responses."""

    def __init__(self, warmup: dict[str, object]):
        self.warmup = warmup
        self.calls: list[tuple[str, object]] = []

    def capabilities(self, project_root, *, warm):
        self.calls.append(("capabilities", warm))
        return {"available": True, "warmed": True, "degraded_reasons": []}

    def check_code(self, code, *, cwd, timeout_s):
        self.calls.append(("check_code", (code, cwd, timeout_s)))
        return self.warmup


def test_health_warm_performs_bounded_real_import_warmup():
    """Mark the service ready only after the Mathlib import succeeds."""
    service = _load_service()
    assert service.WARMUP_TIMEOUT_S == 3600
    probe = _Probe(
        {
            "success": True,
            "ok": True,
            "has_errors": False,
            "timed_out": False,
            "elapsed_s": 3.5,
        }
    )

    response = service._response(probe, {"action": "health", "warm": True})

    assert response["warmed"] is True
    assert response["ready_for_checks"] is True
    assert probe.calls[1][0] == "check_code"
    assert probe.calls[1][1][0] == "import Mathlib"
    assert probe.calls[1][1][2] == 3600


def test_candidate_checks_keep_the_independent_remote_watchdog_cap():
    """Keep candidate compilation bounded even after extending readiness."""
    service = _load_service()
    probe = _Probe({"success": True, "ok": True, "has_errors": False})

    response = service._response(
        probe,
        {"action": "check", "code": "theorem demo : True := by trivial", "timeout_s": 600},
    )

    assert response["success"] is True
    assert probe.calls[0][1][2] == 600


def test_candidate_checks_still_cap_at_the_remote_watchdog_maximum():
    """Keep malformed huge candidate deadlines from escaping the remote guard."""
    service = _load_service()
    probe = _Probe({"success": True, "ok": True, "has_errors": False})

    response = service._response(
        probe,
        {"action": "check", "code": "theorem demo : True := by trivial", "timeout_s": 9999},
    )

    assert response["success"] is True
    assert probe.calls[0][1][2] == 3600


def test_health_warm_exposes_unready_import_failure():
    """Keep transport availability distinct from honest check readiness."""
    service = _load_service()
    probe = _Probe(
        {
            "success": False,
            "ok": False,
            "has_errors": True,
            "timed_out": True,
            "error": "timed out",
            "elapsed_s": 120.0,
        }
    )

    response = service._response(probe, {"action": "health", "warm": True})

    assert response["available"] is True
    assert response["warmed"] is False
    assert response["ready_for_checks"] is False
    assert "LeanProbe import warmup failed" in response["degraded_reasons"]
