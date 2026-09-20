"""Reserve bounded request quota atomically across campaign workers.

The independent quota ledger records conservative estimates, not USD payments.
Provider timeouts and unmetered responses retain their entire hold until an
operator reconciles a request-correlated receipt. Account-wide quota deltas
cannot be attributed to a batch when the API key is shared with other clients.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.accounting.provider_quota import (
    ProviderQuotaError,
    ProviderQuotaQuote,
    admit_quota_reservation,
    load_provider_quota_quote,
)
from core.utils import atomic_json_write

try:
    import fcntl
except ImportError:  # pragma: no cover - controller requires POSIX file locks.
    fcntl = None  # type: ignore[assignment]

BUDGET_PATH_ENV = "LEANFLOW_PROVIDER_QUOTA_BUDGET_PATH"
QUOTE_PATH_ENV = "LEANFLOW_PROVIDER_QUOTA_QUOTE_PATH"
IGNORE_UNKNOWN_HOLDS_ENV = "LEANFLOW_PROVIDER_QUOTA_IGNORE_UNKNOWN_HOLDS"
_THREAD_LOCK = threading.RLock()


class ProviderQuotaBudgetExceeded(ProviderQuotaError):
    """Stop new provider calls because conservative quota capacity is exhausted."""


def _now() -> str:
    """Return an auditable UTC timestamp without touching the campaign ledger."""
    return datetime.now(UTC).isoformat()


@contextmanager
def _budget_lock(path: Path) -> Iterator[None]:
    """Serialize quota transactions across threads and worker processes."""
    if fcntl is None:
        raise ProviderQuotaError("provider quota budget requires POSIX file locking")
    path.parent.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK, path.with_suffix(path.suffix + ".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _read(path: Path, *, ignore_unknown_holds: bool = False) -> dict[str, Any]:
    """Validate a quota ledger and derive conservative or success-only capacity.

    Unknown reservations remain durable evidence and are always exposed through
    ``unknown_hold_quota``. The opt-in success-only mode excludes them only
    from admission capacity; it never deletes or rewrites those reservations.
    """
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProviderQuotaError("provider quota budget is missing or invalid") from exc
    if (
        not isinstance(state, dict)
        or state.get("version") != 1
        or state.get("unit") != "provider_quota"
        or not isinstance(state.get("reservations"), dict)
    ):
        raise ProviderQuotaError("provider quota budget has an invalid schema")
    for field in ("limit_quota", "charges_quota"):
        value = state.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ProviderQuotaError(f"invalid budget {field}")
    held = 0
    unknown_hold = 0
    for item in state["reservations"].values():
        if not isinstance(item, dict) or item.get("status") not in {
            "reserved",
            "unknown",
            "charged",
        }:
            raise ProviderQuotaError("invalid provider quota reservation")
        if item["status"] in {"reserved", "unknown"}:
            value = item.get("reserved_quota")
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ProviderQuotaError("invalid held quota")
            held += value
            if item["status"] == "unknown":
                unknown_hold += value
    state["held_quota"] = held
    state["unknown_hold_quota"] = unknown_hold
    effective_held = held - unknown_hold if ignore_unknown_holds else held
    state["effective_held_quota"] = effective_held
    state["quota_accounting_mode"] = "success_only" if ignore_unknown_holds else "conservative"
    state["remaining_quota"] = max(
        0, state["limit_quota"] - state["charges_quota"] - effective_held
    )
    return state


def initialize_quota_budget(path: str | Path, *, limit_quota: int) -> dict[str, Any]:
    """Create a dedicated quota budget or resume the same immutable limit."""
    if isinstance(limit_quota, bool) or not isinstance(limit_quota, int) or limit_quota <= 0:
        raise ProviderQuotaError("quota budget limit must be a positive integer")
    resolved = Path(path).expanduser().resolve()
    with _budget_lock(resolved):
        if resolved.exists():
            state = _read(resolved)
            if state["limit_quota"] != limit_quota:
                raise ProviderQuotaError("existing quota budget limit cannot be reset")
            return state
        atomic_json_write(
            resolved,
            {
                "version": 1,
                "unit": "provider_quota",
                "limit_quota": limit_quota,
                "charges_quota": 0,
                "reservations": {},
                "created_at": _now(),
            },
        )
        return _read(resolved)


def read_quota_budget(path: str | Path, *, ignore_unknown_holds: bool = False) -> dict[str, Any]:
    """Return quota capacity under an explicit accounting policy."""
    resolved = Path(path).expanduser().resolve()
    with _budget_lock(resolved):
        return _read(resolved, ignore_unknown_holds=ignore_unknown_holds)


@dataclass(frozen=True)
class QuotaReservation:
    """Own one durable pre-request hold; unresolved requests never cost zero."""

    budget_path: Path
    reservation_id: str
    quote: ProviderQuotaQuote

    def settle(self, *, prompt_tokens: int, completion_tokens: int, status: str) -> dict[str, Any]:
        """Charge successful usage conservatively; retain the full hold otherwise.

        Reported usage on failed requests is stored as evidence but does not
        release any capacity. Settlement is idempotent. A charge above its
        reservation closes the budget to further calls for investigation.
        """
        with _budget_lock(self.budget_path):
            state = _read(self.budget_path)
            item = state["reservations"].get(self.reservation_id)
            if not isinstance(item, dict):
                raise ProviderQuotaError("provider quota reservation was lost")
            if item["status"] != "reserved":
                return dict(item)
            item["completed_at"] = _now()
            item["provider_status"] = str(status)
            item["reported_prompt_tokens"] = prompt_tokens
            item["reported_completion_tokens"] = completion_tokens
            valid_usage = (
                not isinstance(prompt_tokens, bool)
                and isinstance(prompt_tokens, int)
                and prompt_tokens > 0
                and not isinstance(completion_tokens, bool)
                and isinstance(completion_tokens, int)
                and completion_tokens >= 0
            )
            if status != "ok" or not valid_usage:
                item["status"] = "unknown"
            else:
                charge = self.quote.estimate_quota(
                    base_url=self.quote.base_url,
                    model=self.quote.model,
                    group=self.quote.group,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
                item["status"] = "charged"
                item["charged_quota"] = charge
                item["cost_source"] = "provider_quote_uncached_usage_estimate"
                state["charges_quota"] += charge
                if charge > item["reserved_quota"]:
                    state["halt_reason"] = "reported usage exceeded conservative reservation"
                    item["reservation_overrun"] = True
            atomic_json_write(self.budget_path, state)
            return dict(item)


def reserve_provider_request(
    *,
    prompt: str,
    system_prompt: str,
    max_output_tokens: int,
    base_url: str,
    model: str,
    environ: Mapping[str, str] | None = None,
) -> QuotaReservation | None:
    """Reserve a quoted request before dispatch, or remain disabled when unset.

    UTF-8 byte counts plus framing headroom conservatively bound the two
    caller-visible messages. Output uses the enforced request token cap.
    Provider-added hidden input, route changes, and price changes require
    validation; this is a conservative quota gate, not a hard billing promise.
    """
    env = os.environ if environ is None else environ
    budget_path, quote_path = str(env.get(BUDGET_PATH_ENV, "")), str(env.get(QUOTE_PATH_ENV, ""))
    if not budget_path and not quote_path:
        return None
    if not budget_path or not quote_path:
        raise ProviderQuotaError("quota gate requires both budget and quote paths")
    for name in ("LEANFLOW_AUXILIARY_RETRY_COUNT", "LEANFLOW_AUXILIARY_TIMEOUT_RETRY_COUNT"):
        if str(env.get(name, "")) != "0":
            raise ProviderQuotaError("quota-gated calls require auxiliary retries disabled")
    quote = load_provider_quota_quote(quote_path)
    ignore_unknown_holds = str(env.get(IGNORE_UNKNOWN_HOLDS_ENV, "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    # The provider may prepend substantial hidden context (retrieval,
    # wrappers, routing metadata). Use the empirically validated allowance
    # from the quote as an operational bound; this remains conservative and
    # any reported overrun still halts the budget for reconciliation.
    prompt_bound = max(
        len(prompt.encode()) + len(system_prompt.encode()) + 768,
        quote.provider_input_allowance_tokens,
    )
    requested = quote.estimate_quota(
        base_url=base_url,
        model=model,
        group=quote.group,
        prompt_tokens=prompt_bound,
        completion_tokens=max(max_output_tokens, quote.provider_output_allowance_tokens),
    )
    resolved = Path(budget_path).expanduser().resolve()
    reservation_id = uuid.uuid4().hex
    with _budget_lock(resolved):
        state = _read(resolved, ignore_unknown_holds=ignore_unknown_holds)
        if state.get("halt_reason"):
            raise ProviderQuotaBudgetExceeded(str(state["halt_reason"]))
        try:
            admit_quota_reservation(
                limit_quota=state["limit_quota"],
                charged_quota=state["charges_quota"],
                outstanding_quota=state["effective_held_quota"],
                requested_quota=requested,
            )
        except ProviderQuotaError as exc:
            raise ProviderQuotaBudgetExceeded(str(exc)) from exc
        state["reservations"][reservation_id] = {
            "status": "reserved",
            "reserved_quota": requested,
            "prompt_token_bound": prompt_bound,
            "max_output_tokens": max_output_tokens,
            "completion_token_bound": max(
                max_output_tokens, quote.provider_output_allowance_tokens
            ),
            "base_url": quote.base_url,
            "model": model,
            "group": quote.group,
            "pricing_version": quote.pricing_version,
            "batch_id": str(env.get("LEANFLOW_CAMPAIGN_BATCH_ID", "")),
            "worker_id": str(env.get("LEANFLOW_CAMPAIGN_WORKER_ID", "")),
            "created_at": _now(),
        }
        atomic_json_write(resolved, state)
    return QuotaReservation(resolved, reservation_id, quote)
