"""Run control-plane auxiliary text calls behind a hard process deadline.

Provider SDK timeouts are transport hints, not wall-clock guarantees: DNS,
socket shutdown, streaming teardown, or an SDK defect can keep a synchronous
request blocked after its nominal timeout.  This module executes one text-only
auxiliary request in an isolated child process and exchanges a small JSON
result over stdio.  The parent owns the deadline and kills the child's entire
process group on timeout or interruption, so no stuck request thread can pin
the managed Lean loop or accumulate inside it.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from agent.providers.auxiliary_client import (
    AuxiliaryCallIdentity,
    _resolve_task_reasoning_effort,
    call_llm,
    resolve_auxiliary_call_identity,
)
from agent.providers.provider_request_audit import audit_request, audit_response
from tools.utilities.interrupt import raise_if_interrupted

RESULT_PREFIX = "LEANFLOW_AUXILIARY_RESULT:"
_MIN_TIMEOUT_S = 0.05
_REAP_TIMEOUT_S = 2.0
_COMMUNICATE_POLL_S = 0.1
_OWNERSHIP_SNAPSHOT_TIMEOUT_S = 0.25
_PROCESS_TOKEN_ENV = "LEANFLOW_INTERNAL_AUXILIARY_PROCESS_TOKEN"
_PROCESS_TOKEN_ATTR = "_leanflow_auxiliary_process_token"

# Error strings cross the worker boundary and may then be persisted in workflow
# telemetry. Sanitize unconditionally here instead of using the configurable
# display redactor: disabling display redaction must never expose credentials in
# a machine artifact.
_NAMED_CREDENTIAL_RE = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization|x-api-key|api[-_ ]?key|"
    r"access[-_ ]?token|refresh[-_ ]?token|auth[-_ ]?token|bearer|token|"
    r"secret|password|passwd|credential|key)\b[\"']?\s*(?::|=)\s*"
    r"[\"']?(?:bearer\s+)?)([^\s&,;\"']+)"
)
_ENV_CREDENTIAL_RE = re.compile(
    r"(?i)(\b[A-Z0-9_]*(?:API_?KEY|ACCESS_?TOKEN|REFRESH_?TOKEN|AUTH_?TOKEN|"
    r"TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)[A-Z0-9_]*\s*=\s*[\"']?)"
    r"([^\s&,;\"']+)"
)
_BEARER_CREDENTIAL_RE = re.compile(r"(?i)(\bbearer\s+)([^\s&,;\"']+)")
_PREFIXED_CREDENTIAL_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:"
    r"sk-[A-Za-z0-9_=.-]{8,}|"
    r"ghp_[A-Za-z0-9_=-]{8,}|"
    r"github_pat_[A-Za-z0-9_=-]{8,}|"
    r"xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"hf_[A-Za-z0-9_=-]{8,}|"
    r"pplx-[A-Za-z0-9_=-]{8,}|"
    r"AKIA[A-Z0-9]{16}"
    r")(?![A-Za-z0-9_-])"
)
_JWT_CREDENTIAL_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?<![A-Za-z0-9_-]\.)"
    r"eyJ[A-Za-z0-9_-]{13,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"
    r"(?![A-Za-z0-9_-])(?!\.[A-Za-z0-9_-])"
)


class IsolatedAuxiliaryError(Exception):
    """Report a failed isolated auxiliary request or worker protocol."""

    def __init__(self, message: str, *, provider: str = "", model: str = "") -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model


class IsolatedAuxiliaryTimeout(TimeoutError):
    """Report that the parent-enforced wall-clock deadline expired."""

    def __init__(self, message: str, *, provider: str = "", model: str = "") -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model


class IsolatedAuxiliaryUnavailable(RuntimeError):
    """Report that the isolated worker could not resolve a provider."""

    def __init__(self, message: str, *, provider: str = "", model: str = "") -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model


class IsolatedAuxiliaryTransientGateway(IsolatedAuxiliaryError):
    """Report a retryable HTTP gateway failure (502/504/524)."""


@dataclass(frozen=True)
class AuxiliaryTextResponse:
    """Carry the normalized text fields needed by control-plane consumers."""

    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


def _timeout_seconds(value: float) -> float:
    """Return a finite positive wall-clock timeout."""
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        timeout = _MIN_TIMEOUT_S
    if not math.isfinite(timeout):
        timeout = _MIN_TIMEOUT_S
    return max(_MIN_TIMEOUT_S, timeout)


def _redact_exact_secrets(value: Any, exact_secrets: Sequence[str] = ()) -> str:
    """Redact exact caller-owned secrets without changing surrounding text."""
    text = str(value or "")
    secrets = sorted(
        {str(secret) for secret in exact_secrets if str(secret)},
        key=len,
        reverse=True,
    )
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    return text


def sanitize_auxiliary_error(
    value: Any,
    *,
    limit: int = 1000,
    exact_secrets: Sequence[str] = (),
) -> str:
    """Return one bounded, unconditionally credential-safe telemetry line."""
    bounded_limit = max(0, int(limit))
    if bounded_limit <= 0:
        return ""
    # Retain lookahead so a credential beginning near the output boundary is
    # redacted as a whole before truncation. Error strings are already resident
    # in memory; this slice only bounds the additional normalization work.
    raw = _redact_exact_secrets(value, exact_secrets)[
        : max(bounded_limit * 4, bounded_limit + 4096)
    ]
    single_line = " ".join(raw.split())
    sanitized = _ENV_CREDENTIAL_RE.sub(r"\1[REDACTED]", single_line)
    sanitized = _NAMED_CREDENTIAL_RE.sub(r"\1[REDACTED]", sanitized)
    sanitized = _BEARER_CREDENTIAL_RE.sub(r"\1[REDACTED]", sanitized)
    sanitized = _PREFIXED_CREDENTIAL_RE.sub("[REDACTED]", sanitized)
    sanitized = _JWT_CREDENTIAL_RE.sub("[REDACTED]", sanitized)
    return sanitized[:bounded_limit]


def _sanitize_identity(value: Any, *, exact_secrets: Sequence[str] = ()) -> str:
    """Return one bounded credential-safe provider or model label."""
    return sanitize_auxiliary_error(value, limit=200, exact_secrets=exact_secrets)


def _reaped_process_group_is_owned(process_group_id: int, process_token: str) -> bool:
    """Return whether the group retains a process with this launch token.

    A reaped leader no longer reserves its PID. Find only members of its old
    process group, then inspect those candidates for the random environment
    token inherited from the isolated worker. This avoids signaling a numeric
    group id that was reused by an unrelated process. Session IDs are not used:
    Darwin reports ``sess=0`` for detached ``setsid`` children after their
    leader exits.
    """
    if os.name != "posix" or process_group_id <= 1 or not process_token:
        return False
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,pgid="],
            check=False,
            capture_output=True,
            text=True,
            timeout=_OWNERSHIP_SNAPSHOT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    candidate_pids: list[str] = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            process_id, process_group = map(int, fields)
        except ValueError:
            continue
        if process_id > 1 and process_group == process_group_id:
            candidate_pids.append(str(process_id))
    if not candidate_pids:
        return False
    try:
        # Darwin's modern ``ps`` interface uses ``-E`` for environment output;
        # the legacy bare ``e`` form can be ignored when combined with ``-o``
        # and therefore hides the inherited launch token.
        environment_flag = "-E" if sys.platform == "darwin" else "e"
        tagged = subprocess.run(
            [
                "ps",
                environment_flag,
                "-ww",
                "-p",
                ",".join(candidate_pids[:256]),
                "-o",
                "command=",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=_OWNERSHIP_SNAPSHOT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return f"{_PROCESS_TOKEN_ENV}={process_token}" in tagged.stdout


def _wait_for_reaped_group(process_group_id: int, process_token: str) -> None:
    """Allow orphaned descendants to be reaped before reporting worker failure."""
    deadline = time.monotonic() + _REAP_TIMEOUT_S
    while time.monotonic() < deadline and _reaped_process_group_is_owned(
        process_group_id, process_token
    ):
        time.sleep(0.02)


def _kill_and_reap(process: subprocess.Popen[str]) -> None:
    """Kill an isolated worker group and reap its root process."""
    if os.name == "posix":
        # Do not call poll() before killpg(): poll reaps an already-exited
        # leader and opens a PID-reuse race. An unreaped leader still reserves
        # its PID; if another caller already reaped it, require a fresh launch-
        # token match before signaling the orphaned group.
        process_token = str(getattr(process, _PROCESS_TOKEN_ATTR, "") or "")
        group_is_owned = process.returncode is None or _reaped_process_group_is_owned(
            process.pid,
            process_token,
        )
        if group_is_owned:
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(process.pid, signal.SIGKILL)
            _wait_for_reaped_group(process.pid, process_token)
    elif process.returncode is None:  # pragma: no cover - Windows fallback
        with contextlib.suppress(OSError):
            process.kill()
    try:
        process.communicate(timeout=_REAP_TIMEOUT_S)
    except subprocess.TimeoutExpired:  # pragma: no cover - SIGKILL should be immediate
        with contextlib.suppress(OSError):
            process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=_REAP_TIMEOUT_S)


def _parse_worker_result(
    stdout: str,
    returncode: int,
    *,
    exact_secrets: Sequence[str] = (),
) -> AuxiliaryTextResponse:
    """Parse one marker-delimited worker result without trusting other stdout."""
    result_line = next(
        (line for line in reversed(stdout.splitlines()) if line.startswith(RESULT_PREFIX)),
        "",
    )
    if not result_line:
        raise IsolatedAuxiliaryError(
            f"isolated auxiliary worker exited with status {returncode} without a result"
        )
    try:
        payload = json.loads(result_line[len(RESULT_PREFIX) :])
    except (TypeError, ValueError) as exc:
        raise IsolatedAuxiliaryError("isolated auxiliary worker returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise IsolatedAuxiliaryError("isolated auxiliary worker returned an invalid result")
    if payload.get("ok") is True:
        return AuxiliaryTextResponse(
            content=_redact_exact_secrets(payload.get("content", ""), exact_secrets),
            model=_sanitize_identity(payload.get("model", ""), exact_secrets=exact_secrets),
            prompt_tokens=max(0, int(payload.get("prompt_tokens", 0) or 0)),
            completion_tokens=max(0, int(payload.get("completion_tokens", 0) or 0)),
            total_tokens=max(0, int(payload.get("total_tokens", 0) or 0)),
        )

    message = (
        sanitize_auxiliary_error(payload.get("error", ""), exact_secrets=exact_secrets)
        or "isolated auxiliary request failed"
    )
    provider = _sanitize_identity(payload.get("provider", ""), exact_secrets=exact_secrets)
    model = _sanitize_identity(payload.get("model", ""), exact_secrets=exact_secrets)
    error_kind = str(payload.get("error_kind", "") or "").strip().lower()
    if error_kind == "timeout":
        raise IsolatedAuxiliaryTimeout(message, provider=provider, model=model)
    if error_kind == "unavailable":
        raise IsolatedAuxiliaryUnavailable(message, provider=provider, model=model)
    if error_kind == "transient_gateway":
        raise IsolatedAuxiliaryTransientGateway(message, provider=provider, model=model)
    raise IsolatedAuxiliaryError(message, provider=provider, model=model)


def run_isolated_auxiliary_text(
    *,
    task: str | None,
    provider: str | None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    messages: list[dict[str, Any]],
    timeout: float,
    temperature: float | None = None,
    max_tokens: int | None = None,
    progress_callback: Callable[[float, float], None] | None = None,
    heartbeat_s: float = 30.0,
    _worker_command: Sequence[str] | None = None,
) -> AuxiliaryTextResponse:
    """Run one text-only auxiliary call under a hard wall-clock deadline.

    The provider-level timeout is forwarded to the worker, but the parent
    independently enforces the same elapsed deadline. ``_worker_command`` is a
    private test seam for exercising stuck and malformed workers without a
    network provider.
    """
    raise_if_interrupted("isolated auxiliary call interrupted before launch")
    timeout_s = _timeout_seconds(timeout)
    request = {
        "task": str(task or ""),
        "provider": str(provider or "") or None,
        "model": str(model or "") or None,
        "base_url": str(base_url or "") or None,
        # Credentials travel only through the child's stdin JSON. They are
        # never included in argv, environment additions, identity labels, or
        # exception messages returned across the worker boundary.
        "api_key": str(api_key or "") or None,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": timeout_s,
    }
    command = list(_worker_command or (sys.executable, "-m", __name__))
    process_token = secrets.token_urlsafe(24)
    worker_env = dict(os.environ)
    worker_env[_PROCESS_TOKEN_ENV] = process_token
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=os.name == "posix",
            env=worker_env,
        )
        setattr(process, _PROCESS_TOKEN_ATTR, process_token)
    except OSError as exc:
        raise IsolatedAuxiliaryError(
            f"failed to start isolated auxiliary worker: {sanitize_auxiliary_error(exc)}"
        ) from exc

    deadline = time.monotonic() + timeout_s
    started_at = time.monotonic()
    normalized_heartbeat_s = max(1.0, float(heartbeat_s or 30.0))
    next_heartbeat_at = normalized_heartbeat_s
    communicate_input: str | None = json.dumps(request, ensure_ascii=False)
    last_timeout: subprocess.TimeoutExpired | None = None
    try:
        while True:
            raise_if_interrupted("isolated auxiliary call interrupted")
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                error = IsolatedAuxiliaryTimeout(f"auxiliary call exceeded {timeout_s:g} seconds")
                if last_timeout is not None:
                    raise error from last_timeout
                raise error
            try:
                stdout, _stderr = process.communicate(
                    communicate_input,
                    timeout=min(_COMMUNICATE_POLL_S, remaining),
                )
            except subprocess.TimeoutExpired as exc:
                # communicate() may be retried after a timeout, but stdin must
                # be supplied only once. The completed call still returns the
                # full buffered output on every supported Python version.
                communicate_input = None
                last_timeout = exc
                elapsed_s = max(0.0, time.monotonic() - started_at)
                if progress_callback is not None and elapsed_s >= next_heartbeat_at:
                    with contextlib.suppress(Exception):
                        progress_callback(elapsed_s, timeout_s)
                    while next_heartbeat_at <= elapsed_s:
                        next_heartbeat_at += normalized_heartbeat_s
                continue
            raise_if_interrupted("isolated auxiliary call interrupted")
            break
    except BaseException:
        # Signals and caller cancellation must not orphan a provider request.
        _kill_and_reap(process)
        raise

    if process.returncode != 0:
        # A worker that crashed after spawning a helper may leave the helper in
        # its isolated group even though the root already exited.
        if os.name == "posix" and (
            _reaped_process_group_is_owned(process.pid, process_token)
            # The ownership probe may be denied in restricted sandboxes.  At
            # this point this freshly reaped worker has a non-zero exit status;
            # kill its just-created process group to avoid stranding children.
            or process.returncode != 0
        ):
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(process.pid, signal.SIGKILL)
            _wait_for_reaped_group(process.pid, process_token)
        raise IsolatedAuxiliaryError(
            f"isolated auxiliary worker exited with status {process.returncode}"
        )
    return _parse_worker_result(
        stdout,
        int(process.returncode or 0),
        exact_secrets=((str(api_key),) if api_key else ()),
    )


def _worker_error_kind(exc: Exception) -> str:
    """Classify worker failures using the existing verification semantics."""
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
    try:
        status_code = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        status_code = None
    text = str(exc or "").lower()
    gateway_codes = {502, 504, 524}
    if status_code in gateway_codes or any(
        f"{code}" in text and ("gateway" in text or "timeout" in text or "origin" in text)
        for code in gateway_codes
    ):
        return "transient_gateway"
    # OpenAI/httpx SDK timeout classes (for example APITimeoutError and
    # ReadTimeout) do not consistently inherit Python's TimeoutError. Keep
    # them on the retryable timeout path using stable class/text markers.
    exception_name = type(exc).__name__.casefold()
    if isinstance(exc, TimeoutError) or "timeout" in exception_name or "timed out" in text:
        return "timeout"
    if isinstance(exc, RuntimeError):
        return "unavailable"
    return "error"


def worker_main() -> int:
    """Execute one stdio request and emit a marker-delimited JSON result."""
    call_kwargs: dict[str, Any] = {}
    exact_secrets: tuple[str, ...] = ()
    try:
        request = json.loads(sys.stdin.read())
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        raw_messages = request.get("messages", [])
        if not isinstance(raw_messages, list):
            raise ValueError("messages must be a list")
        explicit_api_key = str(request.get("api_key", "") or "")
        exact_secrets = (explicit_api_key,) if explicit_api_key else ()
        call_kwargs = {
            "task": str(request.get("task", "") or "") or None,
            "provider": str(request.get("provider", "") or "") or None,
            "model": str(request.get("model", "") or "") or None,
            "base_url": str(request.get("base_url", "") or "") or None,
            "api_key": explicit_api_key or None,
            "messages": raw_messages,
            "temperature": request.get("temperature"),
            "max_tokens": request.get("max_tokens"),
            "timeout": _timeout_seconds(request.get("timeout", _MIN_TIMEOUT_S)),
        }
        response = call_llm(**call_kwargs)
        audit_path = str(os.environ.get("LEANFLOW_PROVIDER_AUDIT_PATH", "") or "").strip()
        if audit_path:
            # Append one secret-free record per child request. A per-process
            # suffix avoids clobbering concurrent workers; the parent can
            # glob the run directory for reconciliation.
            path = f"{audit_path}.{os.getpid()}"
            record = {
                "request": audit_request(
                    raw_messages,
                    model=str(getattr(response, "model", "") or call_kwargs.get("model") or ""),
                    max_tokens=call_kwargs.get("max_tokens"),
                    reasoning_effort=_resolve_task_reasoning_effort(
                        str(call_kwargs.get("task") or "")
                    ),
                    provider=str(call_kwargs.get("provider") or ""),
                    base_url=str(
                        call_kwargs.get("base_url")
                        or os.environ.get("LEANFLOW_OPENAI_BASE_URL", "")
                    ),
                    reservation_id=os.environ.get("LEANFLOW_PROVIDER_QUOTA_RESERVATION_ID", ""),
                ),
                "response": audit_response(response),
            }
            with open(path, "a", encoding="utf-8") as audit_file:
                audit_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        try:
            content = str(response.choices[0].message.content or "").strip()
        except Exception:
            content = ""
        usage = getattr(response, "usage", None)
        prompt_tokens = max(
            0,
            int(getattr(usage, "prompt_tokens", 0) or getattr(usage, "input_tokens", 0) or 0),
        )
        completion_tokens = max(
            0,
            int(getattr(usage, "completion_tokens", 0) or getattr(usage, "output_tokens", 0) or 0),
        )
        total_tokens = max(
            prompt_tokens + completion_tokens,
            int(getattr(usage, "total_tokens", 0) or 0),
        )
        payload = {
            "ok": True,
            "content": _redact_exact_secrets(content, exact_secrets),
            "model": _sanitize_identity(
                getattr(response, "model", ""), exact_secrets=exact_secrets
            ),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
    except Exception as exc:
        try:
            identity = resolve_auxiliary_call_identity(
                task=str(call_kwargs.get("task") or ""),
                provider=str(call_kwargs.get("provider") or ""),
            )
        except Exception:
            identity = AuxiliaryCallIdentity(
                provider=str(call_kwargs.get("provider") or "auto"),
                model="",
            )
        payload = {
            "ok": False,
            "error_kind": _worker_error_kind(exc),
            "error": sanitize_auxiliary_error(exc, exact_secrets=exact_secrets),
            "provider": _sanitize_identity(identity.provider, exact_secrets=exact_secrets),
            "model": _sanitize_identity(identity.model, exact_secrets=exact_secrets),
        }
    sys.stdout.write(RESULT_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess tests
    raise SystemExit(worker_main())
