"""Persist resumable batch campaigns for whole-corpus formalization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

_TERMINAL_BATCH_STATUSES = {"proofs_completed", "completed", "skipped"}
_STATEMENT_COMPLETE_STATUSES = _TERMINAL_BATCH_STATUSES | {"statements_completed"}


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _lease_is_active(batch: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    lease = batch.get("lease")
    if not isinstance(lease, Mapping):
        return False
    expires = _parse_timestamp(lease.get("expires_at"))
    return expires is not None and expires > (now or datetime.now(UTC))


def _attempt_provenance(attempt: Mapping[str, Any]) -> str:
    """Classify historical attempts without rewriting append-only records."""
    explicit = str(attempt.get("provenance", "") or "").strip()
    if explicit:
        return explicit
    if str(attempt.get("cost_scope", "") or "") == "no_provider_call":
        return "manual_gold"
    return "agent"


def _successful_stages(attempts: list[Any], *, provenance: str | None = None) -> set[str]:
    return {
        str(attempt.get("stage", "proofs") or "proofs")
        for attempt in attempts
        if isinstance(attempt, Mapping)
        and bool(attempt.get("success", False))
        and (provenance is None or _attempt_provenance(attempt) == provenance)
    }


def _status_from_stages(stages: set[str]) -> str:
    if "proofs" in stages:
        return "proofs_completed"
    if "statements" in stages:
        return "statements_completed"
    return "pending"


def classify_campaign_failure(attempt: Mapping[str, Any]) -> str:
    """Return a stable coarse failure class for campaign diagnostics."""
    if bool(attempt.get("success", False)):
        return ""
    explicit = str(attempt.get("failure_class", "") or "").strip()
    if explicit:
        return explicit
    reason = str(attempt.get("reason", "") or "").lower()
    if "cost limit" in reason or "budget" in reason:
        return "budget_limit"
    if "timeout" in reason:
        return "verification_timeout"
    if any(
        token in reason
        for token in (
            "infrastructure",
            "provider",
            "unavailable",
            "connection error",
            "connectionerror",
            "api error",
            "apierror",
            "rate limit",
            "too many requests",
        )
    ):
        return "infrastructure"
    stage = str(attempt.get("stage", "proofs") or "proofs")
    return "statement_generation_incomplete" if stage == "statements" else "proof_incomplete"


def _source_batches_for_limit(
    corpus_plan: Mapping[str, Any], batch_item_limit: int
) -> list[dict[str, Any]]:
    """Return stable execution shards without changing dependency order."""
    source_batches = [
        dict(batch)
        for batch in corpus_plan.get("source_batches", []) or []
        if isinstance(batch, Mapping)
    ]
    if batch_item_limit <= 0:
        return source_batches
    positions = {
        str(label): index
        for index, label in enumerate(
            dict(corpus_plan.get("execution_plan", {}) or {}).get("order", []) or []
        )
    }
    shards: list[dict[str, Any]] = []
    for batch in source_batches:
        labels = [str(label) for label in batch.get("labels", []) or []]
        labels.sort(key=lambda label: positions.get(label, len(positions)))
        for start in range(0, len(labels), batch_item_limit):
            selected = labels[start : start + batch_item_limit]
            if not selected:
                continue
            shard_id = "items-" + "-".join(selected)
            shards.append(
                {
                    **batch,
                    "id": shard_id,
                    "labels": selected,
                    "selection_kind": "items",
                }
            )
    return shards


def build_campaign(
    corpus_plan: Mapping[str, Any],
    *,
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable campaign while preserving prior batch attempts and outcomes."""
    prior = dict(existing or {})
    batch_item_limit = max(0, int(prior.get("batch_item_limit", 0) or 0))
    prior_batches = {
        str(batch.get("id", "")): dict(batch)
        for batch in prior.get("batches", []) or []
        if isinstance(batch, Mapping) and batch.get("id")
    }
    execution = dict(corpus_plan.get("execution_plan", {}) or {})
    positions = {
        str(label): index + 1 for index, label in enumerate(execution.get("order", []) or [])
    }
    batches: list[dict[str, Any]] = []
    for source_batch in _source_batches_for_limit(corpus_plan, batch_item_limit):
        if not isinstance(source_batch, Mapping):
            continue
        batch_id = str(source_batch.get("id", "") or "")
        labels = [str(value) for value in source_batch.get("labels", []) or []]
        labels.sort(key=lambda label: positions.get(label, len(positions) + 1))
        previous = prior_batches.get(batch_id, {})
        attempts = list(previous.get("attempts", []) or [])
        successful_stages = _successful_stages(attempts)
        agent_stages = _successful_stages(attempts, provenance="agent")
        manual_stages = _successful_stages(attempts, provenance="manual_gold")
        status = str(previous.get("status", "pending") or "pending")
        if successful_stages:
            status = _status_from_stages(successful_stages)
        completion_provenance = "none"
        if "proofs" in agent_stages and "statements" in agent_stages:
            completion_provenance = "agent_e2e"
        elif "proofs" in manual_stages:
            completion_provenance = "manual_gold"
        elif successful_stages:
            completion_provenance = "mixed_or_partial"
        batches.append(
            {
                "id": batch_id,
                "chapter": str(source_batch.get("chapter", "") or ""),
                "selection_kind": str(
                    source_batch.get("selection_kind", previous.get("selection_kind", "batch"))
                    or "batch"
                ),
                "labels": labels,
                "count": len(labels),
                "status": status,
                "agent_status": _status_from_stages(agent_stages),
                "completion_provenance": completion_provenance,
                "attempts": attempts,
                "last_outcome": dict(previous.get("last_outcome", {}) or {}),
                **({"lease": dict(previous["lease"])} if _lease_is_active(previous) else {}),
            }
        )
    spent = sum(
        float(attempt.get("cost_usd", 0.0) or 0.0)
        for batch in batches
        for attempt in batch["attempts"]
        if isinstance(attempt, Mapping)
    )
    completed = sum(batch["status"] in _TERMINAL_BATCH_STATUSES for batch in batches)
    agent_completed = sum(batch["completion_provenance"] == "agent_e2e" for batch in batches)
    manual_completed = sum(batch["completion_provenance"] == "manual_gold" for batch in batches)
    failure_class_counts: dict[str, int] = {}
    for batch in batches:
        for attempt in batch["attempts"]:
            if not isinstance(attempt, Mapping):
                continue
            failure_class = classify_campaign_failure(attempt)
            if failure_class:
                failure_class_counts[failure_class] = failure_class_counts.get(failure_class, 0) + 1
    return {
        "schema_version": "2",
        "source": str(corpus_plan.get("source", "") or ""),
        "status": "completed" if batches and completed == len(batches) else "active",
        "batch_count": len(batches),
        "completed_batch_count": completed,
        "agent_e2e_completed_batch_count": agent_completed,
        "manual_gold_completed_batch_count": manual_completed,
        "agent_e2e_statement_completed_batch_count": sum(
            batch["agent_status"] in _STATEMENT_COMPLETE_STATUSES for batch in batches
        ),
        "failure_class_counts": failure_class_counts,
        "statement_completed_batch_count": sum(
            batch["status"] in _STATEMENT_COMPLETE_STATUSES for batch in batches
        ),
        "item_count": int(corpus_plan.get("item_count", 0) or 0),
        "batch_item_limit": batch_item_limit,
        "spent_usd": round(spent, 6),
        "budget_usd": prior.get("budget_usd"),
        "cost_policy": {
            "full_run_requires_explicit_budget": True,
            "default_pilot_batch_limit": 2,
            "stop_before_batch_when_budget_would_be_exceeded": True,
        },
        "batches": batches,
    }


def next_campaign_batch(
    campaign: Mapping[str, Any], *, stage: str = "statements"
) -> dict[str, Any] | None:
    """Return the next agent-lane batch independently of paid-action admission.

    ``status`` tracks whether any verified artifact exists, including manual gold.
    Scheduling instead follows ``agent_status`` so a gold proof never masquerades as
    an E2E agent completion or suppresses a future clean-room regression run.
    """
    eligible = (
        {"pending", "retry", "statement_retry"}
        if stage == "statements"
        else {"statements_completed", "proof_retry"}
    )
    if stage not in {"statements", "proofs"}:
        raise ValueError(f"unknown campaign stage: {stage}")
    for batch in campaign.get("batches", []) or []:
        if (
            isinstance(batch, Mapping)
            and batch.get("agent_status", batch.get("status")) in eligible
            and not _lease_is_active(batch)
        ):
            return dict(batch)
    return None


def lease_campaign_batches(
    campaign: Mapping[str, Any],
    *,
    stage: str,
    worker_ids: Sequence[str],
    ttl_seconds: int = 7200,
    now: datetime | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Lease distinct eligible batches without treating them as completed work."""
    if stage not in {"statements", "proofs"}:
        raise ValueError(f"unknown campaign stage: {stage}")
    moment = now or datetime.now(UTC)
    updated = {**campaign, "batches": [dict(item) for item in campaign.get("batches", []) or []]}
    leased: list[dict[str, Any]] = []
    eligible = (
        {"pending", "retry", "statement_retry"}
        if stage == "statements"
        else {"statements_completed", "proof_retry"}
    )
    for worker_id in worker_ids:
        selected = next(
            (
                batch
                for batch in updated["batches"]
                if batch.get("agent_status", batch.get("status")) in eligible
                and not _lease_is_active(batch, now=moment)
            ),
            None,
        )
        if selected is None:
            break
        lease = {
            "worker_id": str(worker_id),
            "stage": stage,
            "leased_at": moment.isoformat(timespec="seconds"),
            "expires_at": (moment + timedelta(seconds=max(1, ttl_seconds))).isoformat(
                timespec="seconds"
            ),
        }
        selected["lease"] = lease
        leased.append(dict(selected))
    return updated, leased


def release_campaign_lease(
    campaign: Mapping[str, Any], *, batch_id: str, worker_id: str = ""
) -> dict[str, Any]:
    """Release a lease, optionally requiring its current owner."""
    updated = {**campaign, "batches": [dict(item) for item in campaign.get("batches", []) or []]}
    for batch in updated["batches"]:
        if str(batch.get("id", "")) != batch_id:
            continue
        lease = batch.get("lease")
        if (
            worker_id
            and isinstance(lease, Mapping)
            and str(lease.get("worker_id", "")) != worker_id
        ):
            raise ValueError(f"campaign batch {batch_id} is leased by another worker")
        batch.pop("lease", None)
        return updated
    raise ValueError(f"unknown campaign batch: {batch_id}")


def record_campaign_outcome(
    campaign: Mapping[str, Any],
    *,
    batch_id: str,
    outcome: Mapping[str, Any],
) -> dict[str, Any]:
    """Return campaign state with one append-only batch attempt and updated status."""
    updated = {**campaign, "batches": [dict(batch) for batch in campaign.get("batches", []) or []]}
    matched = False
    for batch in updated["batches"]:
        if str(batch.get("id", "")) != batch_id:
            continue
        matched = True
        attempt = dict(outcome)
        failure_class = classify_campaign_failure(attempt)
        if failure_class:
            attempt["failure_class"] = failure_class
        attempts = list(batch.get("attempts", []) or [])
        attempts.append(attempt)
        batch["attempts"] = attempts
        batch["last_outcome"] = attempt
        batch.pop("lease", None)
        stage = str(attempt.get("stage", "proofs") or "proofs")
        if stage not in {"statements", "proofs"}:
            raise ValueError(f"unknown campaign outcome stage: {stage}")
        success = bool(attempt.get("success", False))
        if stage == "statements":
            batch["status"] = "statements_completed" if success else "statement_retry"
        else:
            batch["status"] = "proofs_completed" if success else "proof_retry"
        break
    if not matched:
        raise ValueError(f"unknown campaign batch: {batch_id}")
    return build_campaign(
        {
            "source": updated.get("source", ""),
            "item_count": updated.get("item_count", 0),
            "execution_plan": {
                "order": [
                    label for batch in updated["batches"] for label in batch.get("labels", []) or []
                ]
            },
            "source_batches": updated["batches"],
        },
        existing=updated,
    )
