"""Estimate provider quota from an explicit endpoint/model/group quote.

Quota is a provider-defined accounting unit, never implicitly USD. A quote is
an estimate used to reserve request capacity; a correlated provider charge is
the authoritative receipt. Unknown request charges must retain their reserved
capacity until reconciled.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class ProviderQuotaError(ValueError):
    """Reject an invalid quote or a request that exceeds its quota budget."""


def _decimal(value: Any, *, name: str, positive: bool = False) -> Decimal:
    """Validate a finite nonnegative provider ratio without float arithmetic."""
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ProviderQuotaError(f"invalid {name}") from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed == 0):
        raise ProviderQuotaError(f"invalid {name}")
    return parsed


def _nonnegative_integer(value: int, *, name: str) -> None:
    """Reject malformed counts so estimates cannot silently lose usage."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProviderQuotaError(f"{name} must be a nonnegative integer")


def _endpoint(value: str) -> str:
    """Bind a quote to one HTTPS API base without credentials or query strings."""
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ProviderQuotaError("quote requires an explicit HTTPS API base URL")
    return value.rstrip("/")


@dataclass(frozen=True)
class ProviderQuotaQuote:
    """Carry a versioned quota quote scoped to one endpoint, model, and group."""

    base_url: str
    model: str
    group: str
    input_quota_per_token: Decimal
    completion_ratio: Decimal
    cache_read_ratio: Decimal
    pricing_version: str
    evidence_path: str
    observed_at: datetime
    # Empirical allowance for provider-added prompt context (retrieval,
    # wrappers, and routing metadata). This is an operational assumption,
    # not a protocol guarantee; requests beyond it still halt the budget.
    provider_input_allowance_tokens: int = 0
    provider_output_allowance_tokens: int = 0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> ProviderQuotaQuote:
        """Validate provider facts, refusing ambiguous currency or route metadata."""
        if payload.get("unit") != "provider_quota":
            raise ProviderQuotaError("quote unit must be provider_quota")
        model, group = str(payload.get("model", "")), str(payload.get("group", ""))
        if not model or not group:
            raise ProviderQuotaError("quote requires an exact model and group")
        version = str(payload.get("pricing_version", "")).strip()
        if not version:
            raise ProviderQuotaError("quote requires a pricing version")
        try:
            observed = datetime.fromisoformat(str(payload.get("observed_at_utc", "")))
        except ValueError as exc:
            raise ProviderQuotaError("quote requires a valid observation timestamp") from exc
        if observed.tzinfo is None:
            raise ProviderQuotaError("quote observation timestamp must include a timezone")
        age = datetime.now(UTC) - observed
        if age < -timedelta(minutes=5) or age > timedelta(hours=24):
            raise ProviderQuotaError("quote observation is expired or in the future")
        model_ratio = _decimal(payload.get("model_ratio"), name="model_ratio", positive=True)
        group_ratio = _decimal(payload.get("group_ratio"), name="group_ratio", positive=True)
        allowance = payload.get("provider_input_allowance_tokens", 0)
        if isinstance(allowance, bool) or not isinstance(allowance, int) or allowance < 0:
            raise ProviderQuotaError("provider_input_allowance_tokens must be nonnegative")
        output_allowance = payload.get("provider_output_allowance_tokens", 0)
        if (
            isinstance(output_allowance, bool)
            or not isinstance(output_allowance, int)
            or output_allowance < 0
        ):
            raise ProviderQuotaError("provider_output_allowance_tokens must be nonnegative")
        return cls(
            base_url=_endpoint(str(payload.get("base_url", ""))),
            model=model,
            group=group,
            input_quota_per_token=model_ratio * group_ratio,
            completion_ratio=_decimal(
                payload.get("completion_ratio"), name="completion_ratio", positive=True
            ),
            cache_read_ratio=_decimal(payload.get("cache_read_ratio"), name="cache_read_ratio"),
            pricing_version=version,
            evidence_path=str(payload.get("evidence_path", "")),
            observed_at=observed,
            provider_input_allowance_tokens=allowance,
            provider_output_allowance_tokens=output_allowance,
        )

    def estimate_quota(
        self,
        *,
        base_url: str,
        model: str,
        group: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_input_tokens: int = 0,
        conservative: bool = True,
    ) -> int:
        """Estimate a charge, reserving all input at full price by default.

        For preflight, pass a conservative prompt bound and the hard output
        token cap. For postflight estimates, ``conservative=False`` applies
        reported cache reads and provider-style rounding. Both remain estimates
        until a request-correlated quota receipt is recorded.
        """
        if (_endpoint(base_url), model, group) != (self.base_url, self.model, self.group):
            raise ProviderQuotaError("request endpoint/model/group does not match quota quote")
        for name, value in (
            ("prompt_tokens", prompt_tokens),
            ("completion_tokens", completion_tokens),
            ("cached_input_tokens", cached_input_tokens),
        ):
            _nonnegative_integer(value, name=name)
        if cached_input_tokens > prompt_tokens:
            raise ProviderQuotaError("cached input tokens cannot exceed prompt tokens")
        cache_ratio = (
            max(Decimal(1), self.cache_read_ratio) if conservative else self.cache_read_ratio
        )
        input_units = (
            Decimal(prompt_tokens - cached_input_tokens)
            + Decimal(cached_input_tokens) * cache_ratio
        )
        total = (
            input_units + Decimal(completion_tokens) * self.completion_ratio
        ) * self.input_quota_per_token
        rounding = ROUND_CEILING if conservative else ROUND_HALF_UP
        return int(total.to_integral_value(rounding=rounding))


def load_provider_quota_quote(path: str | Path) -> ProviderQuotaQuote:
    """Load an explicit provider quote; never fall back to a global model price."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProviderQuotaError("provider quota quote is missing or invalid") from exc
    if not isinstance(payload, Mapping):
        raise ProviderQuotaError("provider quota quote must be a JSON object")
    return ProviderQuotaQuote.from_mapping(payload)


def admit_quota_reservation(
    *, limit_quota: int, charged_quota: int, outstanding_quota: int, requested_quota: int
) -> None:
    """Reject new work unless its estimate fits after charges and pending holds.

    The controller must call this inside its atomic reservation transaction.
    A timeout or missing receipt keeps its hold in ``outstanding_quota``; it
    does not become a zero-cost request. This function performs no mutation.
    """
    for name, value in (
        ("limit_quota", limit_quota),
        ("charged_quota", charged_quota),
        ("outstanding_quota", outstanding_quota),
        ("requested_quota", requested_quota),
    ):
        _nonnegative_integer(value, name=name)
    if requested_quota == 0 or charged_quota + outstanding_quota + requested_quota > limit_quota:
        raise ProviderQuotaError("provider quota budget does not cover the request reservation")
