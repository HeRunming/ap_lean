#!/usr/bin/env python3
"""Serve bounded LeanProbe checks over stdin JSON lines on the HDP host."""

from __future__ import annotations

import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

from lean_probe import LeanProbe

PROJECT_ROOT = Path("/data/hrm/fate-x-work")
WARMUP_CODE = "import Mathlib"
# Keep the real import readiness probe bounded by the remote watchdog maximum,
# independently from candidate compilation. A candidate compile still retains
# its separate hard 3600-second gate in the caller.
REMOTE_LEAN_TIMEOUT_MAX_S = 3600
WARMUP_TIMEOUT_S = REMOTE_LEAN_TIMEOUT_MAX_S
WARMUP_WORKERS_ENV = "LEANFLOW_REMOTE_WARMUP_WORKERS"
WARMUP_WORKERS_MAX = 4


def _warmup_workers() -> int:
    """Return the bounded LeanProbe session count requested by the caller."""
    try:
        configured = int(os.environ.get(WARMUP_WORKERS_ENV, "1"))
    except (TypeError, ValueError):
        configured = 1
    return max(1, min(configured, WARMUP_WORKERS_MAX))


def _response(probe: LeanProbe, request: dict[str, object]) -> dict[str, object]:
    """Handle health, check, or close requests without trusting client paths."""
    action = str(request.get("action", ""))
    if action == "health":
        warm_requested = bool(request.get("warm", False))
        health = dict(probe.capabilities(PROJECT_ROOT, warm=warm_requested))
        ready_for_checks = bool(health.get("available"))
        if warm_requested and ready_for_checks:
            warmup = probe.check_code(
                WARMUP_CODE,
                cwd=PROJECT_ROOT,
                timeout_s=WARMUP_TIMEOUT_S,
            )
            warmed = bool(
                warmup.get("success")
                and warmup.get("ok")
                and not warmup.get("has_errors")
                and not warmup.get("timed_out")
            )
            health["warmed"] = warmed
            health["warmup"] = {
                "code": WARMUP_CODE,
                "success": bool(warmup.get("success")),
                "ok": bool(warmup.get("ok")),
                "timed_out": bool(warmup.get("timed_out")),
                "error": str(warmup.get("error", "") or ""),
                "elapsed_s": warmup.get("elapsed_s", 0.0),
            }
            ready_for_checks = warmed
            if not warmed:
                reasons = list(health.get("degraded_reasons", []) or [])
                reasons.append("LeanProbe import warmup failed")
                health["degraded_reasons"] = reasons
        health["ready_for_checks"] = ready_for_checks
        return health
    if action == "check":
        code = request.get("code")
        if not isinstance(code, str) or not code:
            return {"available": True, "success": False, "error": "check requires non-empty code"}
        try:
            timeout_s = max(1, min(REMOTE_LEAN_TIMEOUT_MAX_S, int(request.get("timeout_s", 120))))
        except (TypeError, ValueError):
            timeout_s = REMOTE_LEAN_TIMEOUT_MAX_S
        return dict(probe.check_code(code, cwd=PROJECT_ROOT, timeout_s=timeout_s))
    if action == "close":
        return {"available": True, "closing": True}
    return {"available": True, "success": False, "error": f"unknown action: {action}"}


def main() -> int:
    """Keep one LeanProbe instance alive until the client closes stdin."""
    # Third-party Lean tooling must not corrupt the stdout JSONL protocol.
    with redirect_stdout(sys.stderr):
        probe = LeanProbe(
            auto_build=False,
            lake_path="/home/hrm/.elan/bin/lake",
            max_code_sessions=_warmup_workers(),
        )
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("request must be a JSON object")
                with redirect_stdout(sys.stderr):
                    response = _response(probe, request)
                print(json.dumps(response, ensure_ascii=False), flush=True)
                if request.get("action") == "close":
                    break
            except Exception as exc:  # keep protocol alive for the next bounded request
                print(
                    json.dumps({"available": False, "success": False, "error": str(exc)}),
                    flush=True,
                )
    finally:
        with redirect_stdout(sys.stderr):
            probe.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
