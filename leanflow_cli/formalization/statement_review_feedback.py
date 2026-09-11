"""Recover durable statement verdicts and render fresh-generator feedback."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from leanflow_cli.formalization.corpus_campaign import (
    RETRY_CLASS_INFRASTRUCTURE,
    classify_campaign_retry_class,
)

REVIEW_FEEDBACK_ENV = "LEANFLOW_FORMALIZATION_REVIEW_FEEDBACK"


def latest_statement_verdict(batch: Mapping[str, Any]) -> dict[str, Any]:
    """Return the latest statement verdict across intervening infrastructure attempts.

    The ledger's append order is authoritative. Include an unmirrored last
    outcome for legacy ledgers, but never replay an older mirrored BLOCK over
    a newer PASS. A PASS supersedes feedback even if the next action fails.
    """
    attempts = [item for item in batch.get("attempts", []) or [] if isinstance(item, Mapping)]
    last = batch.get("last_outcome")
    if isinstance(last, Mapping) and last not in attempts:
        attempts.append(last)
    for attempt in reversed(attempts):
        if str(attempt.get("stage", "") or "") not in {"", "statements"}:
            continue
        if (
            attempt.get("infrastructure_failure")
            or classify_campaign_retry_class(attempt) == RETRY_CLASS_INFRASTRUCTURE
        ):
            continue
        decision = str(attempt.get("review_decision", "") or "").strip().upper()
        if decision in {"PASS", "BLOCK"}:
            return dict(attempt)
    return {}


def statement_review_feedback(verdict: Mapping[str, Any]) -> str:
    """Render numbered findings with the recorded provider and review type."""
    if str(verdict.get("review_decision", "") or "").strip().upper() != "BLOCK":
        return ""
    provider = str(verdict.get("review_provider", "") or "").strip()
    if not provider:
        providers = verdict.get("statement_providers", {})
        if isinstance(providers, Mapping):
            provider = str(providers.get("judge", "") or "").strip()
    if provider.startswith("deterministic_"):
        review_type = "deterministic statement contract gate"
    elif provider:
        review_type = "independent statement/source review"
    else:
        review_type = "statement/source verdict (review type not recorded)"
    raw = verdict.get("review_findings", []) or []
    findings = [raw] if isinstance(raw, str) else raw
    findings = [str(item).strip() for item in findings if str(item).strip()]
    if not findings:
        diagnostic = str(verdict.get("final_diagnostic", "") or "").strip()
        findings = [
            diagnostic or "BLOCK returned without detailed findings; inspect the review evidence."
        ]
    lines = [
        "[LEANFLOW FORMALIZATION REVIEW FEEDBACK]",
        f"Prior {review_type}: BLOCK.",
        f"Provider: {provider or '[not recorded]'}",
        "Correct these findings before requesting a fresh independent review. "
        "Deterministic lint is not independent review approval.",
        "Findings:",
        *(f"{index}. {finding}" for index, finding in enumerate(findings, start=1)),
    ]
    return "\n".join(lines)[:6000]
