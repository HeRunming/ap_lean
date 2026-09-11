"""Verification provider dispatch for LeanFlow formalization workflows."""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

from agent.accounting.usage_pricing import estimate_cost_usd, has_listed_pricing
from agent.providers.isolated_auxiliary import (
    IsolatedAuxiliaryError,
    IsolatedAuxiliaryTimeout,
    IsolatedAuxiliaryTransientGateway,
    IsolatedAuxiliaryUnavailable,
    resolve_auxiliary_call_identity,
    run_isolated_auxiliary_text,
    sanitize_auxiliary_error,
)
from leanflow_cli.cli.expert_help import (
    is_command_expert_provider,
    normalize_expert_provider,
    run_command_expert_help,
)
from leanflow_cli.workflows.workflow_state import append_workflow_activity
from tools.utilities.interrupt import raise_if_interrupted

logger = logging.getLogger(__name__)

BLUEPRINT_VERIFICATION_TASK = "blueprint_verification"
AUTOFORMALIZER_VERIFICATION_TASK = "autoformalizer_verification"
VERIFICATION_TASKS = {
    BLUEPRINT_VERIFICATION_TASK,
    AUTOFORMALIZER_VERIFICATION_TASK,
}
ADVISORY_VERIFICATION_TIMEOUT_ENV = "LEANFLOW_ADVISORY_VERIFICATION_TIMEOUT_S"
ADVISORY_VERIFICATION_TIMEOUT_DEFAULT_S = 180
ADVISORY_VERIFICATION_TIMEOUT_MAX_S = 3600
AUXILIARY_RETRY_BACKOFF_ENV = "LEANFLOW_AUXILIARY_RETRY_BACKOFFS"
AUXILIARY_RETRY_COUNT_ENV = "LEANFLOW_AUXILIARY_RETRY_COUNT"
AUXILIARY_RETRY_COUNT_DEFAULT = 2
AUXILIARY_RETRY_COUNT_MAX = 3
AUXILIARY_TIMEOUT_RETRY_COUNT_ENV = "LEANFLOW_AUXILIARY_TIMEOUT_RETRY_COUNT"
AUXILIARY_TIMEOUT_RETRY_COUNT_DEFAULT = 1
AUXILIARY_TIMEOUT_RETRY_COUNT_MAX = 2

# A model call cannot be served by the deterministic "local" verifier. Stages
# that require a model resolve it to the main-agent credential chain instead.
MODEL_VERIFICATION_FALLBACK_PROVIDER = "main"

LOCAL_VERIFIER_ALIASES = {
    "deterministic",
    "deterministic-local",
    "lean",
    "lean-kernel",
    "local",
    "local-verifier",
}


@dataclass(frozen=True)
class VerificationReviewResult:
    task: str
    provider: str
    mode: str
    response: str
    status: str
    command: list[str]
    exit_status: int | None
    truncated: bool
    response_chars: int
    max_response_chars: int
    timed_out: bool = False
    model: str = ""
    error: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    pricing_known: bool = False
    cost_source: str = "cost_unavailable"
    transient_gateway_failure: bool = False
    retry_attempts: int = 0
    failure_class: str = ""


def normalize_verification_provider(value: str) -> str:
    normalized = normalize_expert_provider(str(value or ""))
    if not normalized:
        return "local"
    if normalized in LOCAL_VERIFIER_ALIASES:
        return "local"
    if normalized in {"model", "rpc"}:
        return "main"
    return normalized


def default_verification_provider(task: str) -> str:
    if str(task or "") == BLUEPRINT_VERIFICATION_TASK:
        return "main"
    return "local"


def resolve_verification_provider(task: str, explicit: str | None = None) -> str:
    from leanflow_cli.cli.expert_help import resolve_expert_provider

    task_name = str(task or "").strip()
    provider = normalize_verification_provider(
        resolve_expert_provider(task_name, explicit=explicit)
    )
    if provider == "auto":
        return default_verification_provider(task_name)
    return provider or default_verification_provider(task_name)


def resolve_model_verification_provider(task: str, explicit: str | None = None) -> str:
    """Resolve a provider for a stage that cannot run without a model.

    ``local`` names the deterministic Lean/blueprint checks, not a backend that
    can answer a prompt, and it is the configured default for
    ``autoformalizer_verification``.  Stages that are inherently model work
    (retrieval planning, statement generation, source-fidelity judging) must
    therefore fall back to the main-agent credentials instead of failing the
    action with "No LLM provider configured ... provider=local".
    """
    provider = resolve_verification_provider(task, explicit)
    if provider == "local":
        return MODEL_VERIFICATION_FALLBACK_PROVIDER
    return provider


def is_local_verification_provider(provider: str) -> bool:
    return normalize_verification_provider(provider) == "local"


def is_command_verification_provider(provider: str) -> bool:
    return is_command_expert_provider(normalize_verification_provider(provider))


def advisory_verification_timeout_s() -> int:
    """Return the bounded deadline for non-authoritative verifier advice."""
    try:
        configured = int(str(os.getenv(ADVISORY_VERIFICATION_TIMEOUT_ENV, "") or "").strip())
    except (TypeError, ValueError):
        configured = ADVISORY_VERIFICATION_TIMEOUT_DEFAULT_S
    return max(5, min(configured, ADVISORY_VERIFICATION_TIMEOUT_MAX_S))


def verification_review_timeout_s(value: int | float | None = None) -> int:
    """Return a review deadline bounded to a conservative operational cap."""
    if value is None:
        raw = os.getenv(ADVISORY_VERIFICATION_TIMEOUT_ENV, "")
        try:
            configured = int(str(raw or "").strip())
        except (TypeError, ValueError):
            configured = ADVISORY_VERIFICATION_TIMEOUT_DEFAULT_S
    else:
        try:
            configured = int(float(value))
        except (TypeError, ValueError):
            configured = ADVISORY_VERIFICATION_TIMEOUT_DEFAULT_S
    return max(5, min(configured, ADVISORY_VERIFICATION_TIMEOUT_MAX_S))


def _auxiliary_retry_delays() -> tuple[float, ...]:
    """Return finite exponential backoffs for transient gateway failures."""
    raw = str(os.getenv(AUXILIARY_RETRY_BACKOFF_ENV, "1,2") or "1,2")
    delays: list[float] = []
    for token in raw.split(","):
        try:
            value = float(token.strip())
        except ValueError:
            continue
        if value >= 0:
            delays.append(min(30.0, value))
    return tuple(delays[:3]) or (1.0, 2.0)


def _auxiliary_retry_count() -> int:
    """Return the bounded number of transient gateway retries."""
    try:
        configured = int(str(os.getenv(AUXILIARY_RETRY_COUNT_ENV, "") or "").strip())
    except (TypeError, ValueError):
        configured = AUXILIARY_RETRY_COUNT_DEFAULT
    return max(0, min(configured, AUXILIARY_RETRY_COUNT_MAX))


def _auxiliary_timeout_retry_count() -> int:
    """Return bounded retries for timeouts that happen before the deadline."""
    try:
        configured = int(str(os.getenv(AUXILIARY_TIMEOUT_RETRY_COUNT_ENV, "") or "").strip())
    except (TypeError, ValueError):
        configured = AUXILIARY_TIMEOUT_RETRY_COUNT_DEFAULT
    return max(0, min(configured, AUXILIARY_TIMEOUT_RETRY_COUNT_MAX))


def _record_verification_activity(event_type: str, message: str, **details: Any) -> None:
    try:
        append_workflow_activity(event_type, message, **details)
    except Exception:
        logger.debug("Failed to append verification telemetry activity", exc_info=True)


def run_command_verification_review(
    *,
    provider: str,
    task: str,
    prompt: str,
    cwd: str = "",
    timeout_s: int | None = None,
) -> VerificationReviewResult:
    """Execute a verification review via a command-based expert provider and return its output and status. Normalizes the provider name, invokes run_command_expert_help with the given prompt and timeout, and constructs a VerificationReviewResult with command execution details including exit status and response truncation. Records telemetry before and after execution."""
    raise_if_interrupted("verification command review interrupted before launch")
    timeout_s = verification_review_timeout_s(timeout_s)
    normalized = normalize_verification_provider(provider)
    review_id = uuid.uuid4().hex
    started_at = time.monotonic()
    _record_verification_activity(
        "verification-review-request",
        "Verification command review started",
        review_id=review_id,
        task=task,
        provider=normalized,
        mode="command",
        cwd=cwd,
        timeout_s=timeout_s,
        elapsed_s=0.0,
        prompt=prompt,
    )
    command_result = run_command_expert_help(
        provider=normalized,
        task=task,
        prompt=prompt,
        cwd=cwd,
        timeout_s=timeout_s,
    )
    raise_if_interrupted("verification command review interrupted after provider return")
    status = (
        "timeout"
        if command_result.timed_out
        else ("ok" if command_result.exit_status == 0 else "error")
    )
    result = VerificationReviewResult(
        task=task,
        provider=command_result.provider,
        mode="command",
        response=command_result.response,
        status=status,
        command=list(command_result.command),
        exit_status=command_result.exit_status,
        truncated=command_result.truncated,
        response_chars=command_result.response_chars,
        max_response_chars=command_result.max_response_chars,
        timed_out=command_result.timed_out,
    )
    _record_verification_activity(
        "verification-review-result",
        "Verification command review finished",
        review_id=review_id,
        task=task,
        provider=result.provider,
        mode=result.mode,
        timeout_s=timeout_s,
        elapsed_s=max(0.0, time.monotonic() - started_at),
        status=result.status,
        command=result.command,
        exit_status=result.exit_status,
        response=result.response,
        truncated=result.truncated,
        response_chars=result.response_chars,
        max_response_chars=result.max_response_chars,
        timed_out=result.timed_out,
    )
    return result


def run_model_verification_review(
    *,
    provider: str,
    model: str = "",
    task: str,
    prompt: str,
    system_prompt: str = "",
    timeout_s: int | None = None,
    max_tokens: int = 12000,
) -> VerificationReviewResult:
    """Execute a verification review via an LLM call, building optional system/user message pair and capturing model response, timeout behavior, and error states. Handles RuntimeError (provider unavailable) and generic exceptions distinctly, returning a VerificationReviewResult with the model's content or appropriate error message. Records telemetry before and after execution."""
    raise_if_interrupted("verification model review interrupted before launch")
    timeout_s = verification_review_timeout_s(timeout_s)
    normalized = normalize_verification_provider(provider)
    effective_provider = None if normalized == "auto" else normalized
    review_id = uuid.uuid4().hex
    started_at = time.monotonic()
    _record_verification_activity(
        "verification-review-request",
        "Verification model review started",
        review_id=review_id,
        task=task,
        provider=normalized,
        mode="model",
        prompt=prompt,
        timeout_s=timeout_s,
        elapsed_s=0.0,
    )
    timed_out = False
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    resolved_provider = normalized
    try:
        identity = resolve_auxiliary_call_identity(
            task=task,
            provider=effective_provider,
            model=model or None,
        )
    except Exception:
        identity = None
    heartbeat_provider = str(getattr(identity, "provider", "") or "").strip() or normalized
    heartbeat_model = str(getattr(identity, "model", "") or "").strip()

    def heartbeat(elapsed_s: float, deadline_s: float) -> None:
        message = (
            f"Verification review still waiting on {heartbeat_provider}"
            f"{f'/{heartbeat_model}' if heartbeat_model else ''} "
            f"({elapsed_s:.0f}s elapsed, {deadline_s:.0f}s deadline)"
        )
        print(f"   ⏳ {message}", flush=True)
        _record_verification_activity(
            "verification-review-heartbeat",
            message,
            review_id=review_id,
            task=task,
            provider=heartbeat_provider,
            model=heartbeat_model,
            mode="model",
            timeout_s=deadline_s,
            elapsed_s=elapsed_s,
        )

    requested_model = str(model or "").strip()
    retry_delays = _auxiliary_retry_delays()
    retry_count = _auxiliary_retry_count()
    timeout_retry_count = _auxiliary_timeout_retry_count()
    transient_gateway_failure = False
    retry_sequence = 0
    gateway_retries = 0
    timeout_retries = 0
    attempts_sent = 0
    last_retry_reason = ""
    deadline = started_at + max(1, int(timeout_s or 0))
    try:
        while True:
            attempt_prompt = prompt
            if retry_sequence:
                # Change the payload on every retry so a gateway/proxy cannot keep
                # replaying an identical request while we still remain bounded.
                attempt_prompt = (
                    f"{prompt}\n\n[Fresh {last_retry_reason} retry {retry_sequence}; "
                    "produce a fresh response and do not replay the previous payload verbatim.]"
                )
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": attempt_prompt})
            if attempts_sent:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise IsolatedAuxiliaryTimeout("auxiliary verification retry window expired")
            else:
                remaining = float(max(1, int(timeout_s or 0)))
            attempts_sent += 1
            try:
                response = run_isolated_auxiliary_text(
                    task=task,
                    provider=effective_provider,
                    model=requested_model or None,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=max_tokens,
                    timeout=remaining,
                    progress_callback=heartbeat,
                )
                break
            except IsolatedAuxiliaryTimeout as exc:
                # A timeout raised immediately by a worker/proxy can recover on
                # a fresh request. A full-duration timeout has no remaining
                # budget and is returned as a durable reviewer timeout.
                if timeout_retries >= timeout_retry_count:
                    raise
                timeout_retries += 1
                retry_sequence += 1
                last_retry_reason = "timeout"
                delay = min(
                    retry_delays[min(retry_sequence - 1, len(retry_delays) - 1)],
                    max(0.0, deadline - time.monotonic()),
                )
                if delay >= max(0.0, deadline - time.monotonic()):
                    raise exc
                _record_verification_activity(
                    "verification-review-timeout-retry",
                    "Reviewer timeout occurred before deadline; retrying with a fresh request",
                    review_id=review_id,
                    task=task,
                    provider=heartbeat_provider,
                    model=requested_model or heartbeat_model,
                    retry_number=retry_sequence,
                    delay_seconds=delay,
                    error=sanitize_auxiliary_error(exc),
                )
                if delay:
                    time.sleep(delay)
            except (IsolatedAuxiliaryTransientGateway, IsolatedAuxiliaryError) as exc:
                error_text = str(exc).lower()
                is_gateway = isinstance(exc, IsolatedAuxiliaryTransientGateway) or (
                    "502" in error_text and "gateway" in error_text
                )
                if not is_gateway or gateway_retries >= retry_count:
                    raise
                transient_gateway_failure = True
                gateway_retries += 1
                retry_sequence += 1
                last_retry_reason = "gateway"
                delay = min(
                    retry_delays[min(retry_sequence - 1, len(retry_delays) - 1)],
                    max(0.0, deadline - time.monotonic()),
                )
                _record_verification_activity(
                    "verification-review-transient-gateway",
                    "Transient HTTP 502/Bad Gateway; retrying with a distinct payload",
                    review_id=review_id,
                    task=task,
                    provider=heartbeat_provider,
                    model=requested_model or heartbeat_model,
                    retry_number=retry_sequence,
                    delay_seconds=delay,
                    error=sanitize_auxiliary_error(exc),
                )
                if delay:
                    time.sleep(delay)
                if delay >= max(0.0, deadline - time.monotonic()):
                    raise IsolatedAuxiliaryTimeout(
                        "auxiliary verification retry window expired",
                        provider=heartbeat_provider,
                        model=requested_model or heartbeat_model,
                    )
        raise_if_interrupted("verification model review interrupted after provider return")
        content = response.content.strip()
        model = requested_model or response.model
        prompt_tokens = max(0, int(response.prompt_tokens or 0))
        completion_tokens = max(0, int(response.completion_tokens or 0))
        total_tokens = max(
            prompt_tokens + completion_tokens,
            int(response.total_tokens or 0),
        )
        status = "ok" if content else "no_answer"
        error = "" if content else "the configured verifier returned no content"
        failure_class = "" if content else "empty_response"
    except IsolatedAuxiliaryTimeout as exc:
        content = ""
        model = sanitize_auxiliary_error(getattr(exc, "model", ""), limit=200)
        resolved_provider = (
            sanitize_auxiliary_error(getattr(exc, "provider", ""), limit=200) or normalized
        )
        status = "timeout"
        error = sanitize_auxiliary_error(exc)
        timed_out = True
        failure_class = "reviewer_timeout"
    except IsolatedAuxiliaryUnavailable as exc:
        content = ""
        model = sanitize_auxiliary_error(getattr(exc, "model", ""), limit=200)
        resolved_provider = (
            sanitize_auxiliary_error(getattr(exc, "provider", ""), limit=200) or normalized
        )
        status = "unavailable"
        error = sanitize_auxiliary_error(exc)
        failure_class = "provider_unavailable"
    except IsolatedAuxiliaryError as exc:
        content = ""
        model = sanitize_auxiliary_error(getattr(exc, "model", ""), limit=200)
        resolved_provider = (
            sanitize_auxiliary_error(getattr(exc, "provider", ""), limit=200) or normalized
        )
        status = "error"
        error = sanitize_auxiliary_error(exc)
        failure_class = "provider_error"
    except RuntimeError as exc:
        content = ""
        model = ""
        status = "unavailable"
        error = sanitize_auxiliary_error(exc)
        failure_class = "provider_unavailable"
    except Exception as exc:
        content = ""
        model = ""
        status = "error"
        error = sanitize_auxiliary_error(f"{type(exc).__name__}: {exc}")
        failure_class = "unexpected_error"

    result = VerificationReviewResult(
        task=task,
        provider=resolved_provider,
        mode="model",
        response=content,
        status=status,
        command=[],
        exit_status=None,
        truncated=False,
        response_chars=len(content),
        max_response_chars=max_tokens,
        timed_out=timed_out,
        model=model,
        error=error,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_usd=estimate_cost_usd(model, prompt_tokens, completion_tokens),
        pricing_known=has_listed_pricing(model),
        cost_source=("auxiliary_token_usage" if has_listed_pricing(model) else "cost_unavailable"),
        transient_gateway_failure=transient_gateway_failure,
        retry_attempts=max(0, attempts_sent - 1),
        failure_class=failure_class,
    )
    _record_verification_activity(
        "verification-review-result",
        "Verification model review finished",
        review_id=review_id,
        task=task,
        provider=result.provider,
        requested_provider=normalized,
        mode=result.mode,
        timeout_s=timeout_s,
        elapsed_s=max(0.0, time.monotonic() - started_at),
        status=result.status,
        response=result.response,
        response_chars=result.response_chars,
        max_response_chars=result.max_response_chars,
        model=result.model,
        error=result.error,
        timed_out=result.timed_out,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
        cost_usd=result.cost_usd,
        pricing_known=result.pricing_known,
        cost_source=result.cost_source,
        transient_gateway_failure=result.transient_gateway_failure,
        retry_attempts=result.retry_attempts,
        failure_class=result.failure_class,
    )
    return result
