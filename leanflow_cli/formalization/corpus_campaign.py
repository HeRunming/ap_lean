"""Persist resumable batch campaigns for whole-corpus formalization."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

_TERMINAL_BATCH_STATUSES = {"proofs_completed", "completed", "skipped"}
_STATEMENT_COMPLETE_STATUSES = _TERMINAL_BATCH_STATUSES | {"statements_completed"}


def _outcome_root_reachable(batch: Mapping[str, Any]) -> bool:
    """Return true only for explicit integration evidence in the latest outcome."""
    outcome = batch.get("last_outcome")
    return isinstance(outcome, Mapping) and outcome.get("root_reachable") is True


# A batch that has failed one stage this many times is not converging. Nine
# runaway batches consumed 3,549 of 14,652 statement attempts in the HDP run
# because nothing capped them, and each also held its downstream batches at
# pending for the whole campaign.
MAX_STAGE_FAILURES_BEFORE_TERMINAL = 10

# Hitting the cap does not prove the item is unformalizable — only that the
# bounded lane cannot get there. The batches that hit it are disproportionately
# the heavily-cited foundations, so parking them outright strands every
# downstream item. They get exactly one attempt in an unbounded, full-tool lane
# before being parked for good.
ESCALATION_STATUS = "statement_escalate"

# The unbounded lane runs the full agent (book search, Mathlib exploration,
# multi-declaration output), which does not fit in the bounded lane's per-action
# reservation: every escalated attempt in the first HDP escalation wave died on
# "Per-action USD cost limit reached" at $1.5-1.9 against a $2.0 reserve.
ESCALATION_COST_MULTIPLIER = 4.0

# Stable labels used by the campaign ledger to distinguish a provider/worker
# retry from a semantic source-fidelity retry.  ``failure_class`` retains the
# more specific diagnosis; ``retry_class`` is the routing decision.
RETRY_CLASS_SEMANTIC = "semantic"
RETRY_CLASS_INFRASTRUCTURE = "infrastructure"
RETRY_CLASS_TERMINAL = "terminal"

# Default policy; an operator may raise/lower it per escalation process via the
# durable ``max_semantic_repairs`` receipt field. The default remains one fresh
# generator/reviewer boundary per semantic BLOCK.
MAX_ESCALATION_SEMANTIC_REPAIRS = 1

# An escalated attempt that died on budget or transport says nothing about
# whether the item is formalizable, so it must not consume the one escalation
# turn. Bound the resulting retries so a persistently broken provider cannot
# loop a batch forever.
MAX_ESCALATION_ATTEMPTS = 3

# A printed-book citation ("Theorem 4.4.3") names the same item the campaign
# tracks under its bare numeric label ("4.4.3").
_SOURCE_REFERENCE_LABEL_RE = re.compile(
    r"^\s*(?:Proposition|Theorem|Lemma|Definition|Corollary|Exercise|Example|Remark|Section)"
    r"\s+\$?(\d+(?:\.\d+)+)\$?\s*$",
    flags=re.IGNORECASE,
)


def source_reference_to_label(reference: str) -> str:
    """Return the campaign label a printed-book citation refers to, or ""."""
    match = _SOURCE_REFERENCE_LABEL_RE.match(str(reference or ""))
    return match.group(1) if match else ""


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


def _batch_dependencies_ready(
    batch: Mapping[str, Any],
    *,
    stage: str,
    label_statuses: Mapping[str, str],
) -> bool:
    """Return whether every declared predecessor reached the required agent stage.

    Hard dependencies are stage-sensitive: statement generation needs an
    available statement, while proof generation needs a proved predecessor.
    A skipped prerequisite never silently authorizes a proof that relies on it.
    """
    required = (
        _STATEMENT_COMPLETE_STATUSES
        if stage == "statements"
        else _TERMINAL_BATCH_STATUSES - {"skipped"}
    )
    return all(
        label_statuses.get(str(label), "pending") in required
        for label in batch.get("dependency_labels", []) or []
    )


def _batch_soft_dependencies_ready(
    batch: Mapping[str, Any],
    *,
    stage: str,
    label_statuses: Mapping[str, str],
) -> bool:
    """Return whether inferred foundations are available for reuse this wave.

    Soft dependencies also require only statements_completed, consistent with
    the hard-dependency relaxation above.
    """
    required = _STATEMENT_COMPLETE_STATUSES
    return all(
        label_statuses.get(str(label), "pending") in required
        for label in batch.get("soft_dependency_labels", []) or []
    )


def _statement_escalation_spent(attempts: Sequence[Any]) -> bool:
    """Return whether the unbounded statement lane has used up its turns.

    ``escalated`` is a receipt written by the runner for an attempt that actually
    ran in the unbounded lane; ``escalate`` is only the request that routes the
    batch there. Counting requests would burn the allowance before the lane ever
    ran. An attempt that died on budget or transport also never exercised the
    lane, so it does not count either -- otherwise a misconfigured cost ceiling
    silently parks every heavily-cited foundation, which is what happened on the
    first HDP escalation wave.
    """
    receipts = [
        item
        for item in attempts
        if isinstance(item, Mapping) and bool(item.get("escalated", False))
    ]
    semantic = 0
    for item in receipts:
        # ``retry_class`` is persisted by ``record_campaign_outcome``. Legacy
        # receipts are classified from their detailed failure fields here.
        retry_class = str(item.get("retry_class", "") or "").strip()
        if not retry_class:
            retry_class = classify_campaign_retry_class(item)
        if retry_class == RETRY_CLASS_SEMANTIC:
            semantic += 1
    # A semantic escalation is deliberately bounded (one by default). The
    # outcome recorder may use a larger configured semantic limit, while
    # infrastructure deaths remain separately bounded by receipt count.
    return semantic >= 1 or len(receipts) >= MAX_ESCALATION_ATTEMPTS


def statement_escalation_pending(batch: Mapping[str, Any]) -> bool:
    """Return whether this batch is waiting on an unbounded statement attempt."""
    return str(batch.get("status", "") or "") == ESCALATION_STATUS


def _attempt_is_terminal_skip(attempt: Any) -> bool:
    """Return whether an attempt recorded a permanently unretryable outcome."""
    return (
        isinstance(attempt, Mapping)
        and not bool(attempt.get("success", False))
        and bool(attempt.get("terminal", False))
    )


def _attempt_provenance(attempt: Mapping[str, Any]) -> str:
    """Classify historical attempts without rewriting append-only records."""
    explicit = str(attempt.get("provenance", "") or "").strip()
    if explicit:
        return explicit
    if str(attempt.get("cost_scope", "") or "") == "no_provider_call":
        return "manual_gold"
    return "agent"


def _successful_stages(attempts: list[Any], *, provenance: str | None = None) -> set[str]:
    latest_by_stage: dict[str, Mapping[str, Any]] = {}
    for attempt in attempts:
        if isinstance(attempt, Mapping):
            latest_by_stage[str(attempt.get("stage", "proofs") or "proofs")] = attempt
    return {
        str(attempt.get("stage", "proofs") or "proofs")
        for attempt in latest_by_stage.values()
        if bool(attempt.get("success", False))
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
    # A deterministic terminal verdict is authoritative: it was decided without
    # a provider call, so reason-text keyword sniffing must not reclassify it as
    # a retryable infrastructure fault.
    explicit_terminal = str(attempt.get("failure_class", "") or "").strip()
    if bool(attempt.get("terminal", False)) and explicit_terminal:
        return explicit_terminal
    # A worker cancellation/timeout is an infrastructure event even when its
    # diagnostic contains the word ``timeout``.  The explicit marker prevents
    # it from being misclassified as a mathematical verification timeout and
    # keeps cancellation receipts distinguishable from ordinary retries.
    if bool(attempt.get("infrastructure_failure", False)):
        return explicit_terminal or "infrastructure"
    # A reviewer BLOCK is a semantic verdict even when its diagnostic mentions
    # a timeout or another infrastructure-shaped word. Preserve it as a
    # semantic retry so the campaign cannot silently spend the escalation lane
    # on an unbounded same-session repair loop.
    if str(attempt.get("review_decision", "") or "").strip().upper() == "BLOCK":
        return "semantic_review_block"
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
            "cannot claim workflow live status",
            "workflow live owner",
            "owner conflict",
            # An external kill says nothing about the mathematics. ``headless
            # early exit`` is deliberately NOT here: it is the exit path the
            # runner takes when the verifier returned BLOCK, so it carries a real
            # verdict. That verdict now reaches the ledger as review_decision plus
            # findings, and treating it as infrastructure would let a genuinely
            # blocked item retry forever without consuming its allowance.
            "signal interrupt",
        )
    ):
        return "infrastructure"
    explicit = str(attempt.get("failure_class", "") or "").strip()
    if explicit:
        return explicit
    stage = str(attempt.get("stage", "proofs") or "proofs")
    return "statement_generation_incomplete" if stage == "statements" else "proof_incomplete"


def classify_campaign_retry_class(attempt: Mapping[str, Any]) -> str:
    """Return the routing class for one campaign attempt.

    This intentionally sits beside ``classify_campaign_failure``: callers that
    need the detailed diagnosis keep it, while schedulers can make the simpler
    semantic-versus-infrastructure decision without matching free-form text.
    """
    if bool(attempt.get("success", False)):
        return ""
    explicit = str(attempt.get("retry_class", "") or "").strip().lower()
    if explicit in {
        RETRY_CLASS_SEMANTIC,
        RETRY_CLASS_INFRASTRUCTURE,
        RETRY_CLASS_TERMINAL,
    }:
        return explicit
    if bool(attempt.get("terminal", False)):
        return RETRY_CLASS_TERMINAL
    failure = classify_campaign_failure(attempt)
    if failure in {
        "infrastructure",
        "budget_limit",
        "provider_unavailable",
        "transport",
    } or bool(attempt.get("infrastructure_failure", False)):
        return RETRY_CLASS_INFRASTRUCTURE
    return RETRY_CLASS_SEMANTIC


def _batch_stage_priority(
    batch: Mapping[str, Any], *, stage: str
) -> tuple[int, float, int, int, int, int]:
    """Prefer untouched/cheap work over repeatedly expensive local blockers."""
    attempts = [
        attempt
        for attempt in batch.get("attempts", []) or []
        if isinstance(attempt, Mapping) and str(attempt.get("stage", "proofs") or "proofs") == stage
    ]
    cost = sum(max(0.0, float(attempt.get("cost_usd", 0.0) or 0.0)) for attempt in attempts)
    failures = sum(not bool(attempt.get("success", False)) for attempt in attempts)
    source_complexity = max(0, int(batch.get("source_complexity_score", 0) or 0))
    proof_obligations = max(
        0,
        int(dict(batch.get("last_outcome", {}) or {}).get("proof_obligations", 0) or 0),
    )
    stage_complexity = (
        proof_obligations if stage == "proofs" and proof_obligations else source_complexity
    )
    # An escalation-pending batch is by construction the most expensive and most
    # failed thing in the frontier, so the cheap-first ordering below would rank
    # it last -- behind every batch that is blocked on it. Escalations are the
    # heavily-cited foundations, so give them their own leading tier: unblocking
    # one is worth more than another cheap leaf.
    tier = 0 if statement_escalation_pending(batch) else 1
    return (tier, round(cost, 9), failures, stage_complexity, source_complexity, len(attempts))


def batch_stage_attempt_count(batch: Mapping[str, Any], *, stage: str) -> int:
    """Return durable attempts for one stage, including infrastructure outcomes."""
    return sum(
        1
        for attempt in batch.get("attempts", []) or []
        if isinstance(attempt, Mapping) and str(attempt.get("stage", "proofs") or "proofs") == stage
    )


def _select_economic_frontier(
    frontier: Sequence[Mapping[str, Any]],
    *,
    stage: str,
    label_statuses: Mapping[str, str],
    allow_unready_soft: bool = True,
) -> Mapping[str, Any] | None:
    """Select the cheapest resumable batch while respecting hard dependencies."""
    soft_ready = [
        batch
        for batch in frontier
        if _batch_soft_dependencies_ready(batch, stage=stage, label_statuses=label_statuses)
    ]
    candidates = soft_ready or (list(frontier) if allow_unready_soft else [])
    return min(
        candidates, key=lambda batch: _batch_stage_priority(batch, stage=stage), default=None
    )


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
        if str(batch.get("selection_kind", "batch") or "batch") == "document":
            shards.append(batch)
            continue
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
    item_metadata = {
        str(item.get("label", "") or ""): dict(item)
        for item in corpus_plan.get("items", []) or []
        if isinstance(item, Mapping) and str(item.get("label", "") or "").strip()
    }
    positions = {
        str(label): index + 1 for index, label in enumerate(execution.get("order", []) or [])
    }
    declared_dependencies: dict[str, set[str]] = {}
    soft_dependencies: dict[str, set[str]] = {}
    for edge in corpus_plan.get("dependency_edges", []) or []:
        if not isinstance(edge, Mapping):
            continue
        dependencies = None
        if edge.get("status") == "declared_unverified":
            dependencies = declared_dependencies
        elif edge.get("status") == "candidate":
            dependencies = soft_dependencies
        if dependencies is not None:
            dependencies.setdefault(str(edge.get("from", "")), set()).add(str(edge.get("to", "")))
    batches: list[dict[str, Any]] = []
    known_batch_labels = {
        str(label)
        for source_batch in _source_batches_for_limit(corpus_plan, batch_item_limit)
        for label in source_batch.get("labels", []) or []
    }
    for source_batch in _source_batches_for_limit(corpus_plan, batch_item_limit):
        if not isinstance(source_batch, Mapping):
            continue
        batch_id = str(source_batch.get("id", "") or "")
        labels = [str(value) for value in source_batch.get("labels", []) or []]
        labels.sort(key=lambda label: positions.get(label, len(positions) + 1))
        previous = prior_batches.get(batch_id, {})
        source_complexity_score = max(
            [
                int(item_metadata.get(label, {}).get("source_complexity_score", 0) or 0)
                for label in labels
            ]
            + [int(previous.get("source_complexity_score", 0) or 0)]
        )
        source_subpart_count = max(
            sum(
                int(item_metadata.get(label, {}).get("source_subpart_count", 0) or 0)
                for label in labels
            ),
            int(previous.get("source_subpart_count", 0) or 0),
        )
        label_set = set(labels)
        derived_dependencies = {
            dependency
            for label in labels
            for dependency in declared_dependencies.get(label, set())
            if dependency and dependency not in label_set
        }
        if not derived_dependencies:
            derived_dependencies = {
                str(value)
                for value in (
                    source_batch.get("dependency_labels", previous.get("dependency_labels", []))
                    or []
                )
                if str(value) and str(value) not in label_set
            }
        # The source-context preflight discovers citations the static dependency
        # graph missed ("Theorem 4.4.3" cited in prose but absent from the uses
        # field). Feeding them back as real dependencies is what stops the batch
        # from being rescheduled into the same zero-cost rejection every wave.
        for attempt in previous.get("attempts", []) or []:
            if not isinstance(attempt, Mapping):
                continue
            for reference in attempt.get("missing_source_references", []) or []:
                discovered = source_reference_to_label(str(reference))
                if discovered and discovered not in label_set:
                    derived_dependencies.add(discovered)
        dependency_labels = sorted(
            derived_dependencies,
            key=lambda label: positions.get(label, len(positions) + 1),
        )
        unresolved_dependency_labels = [
            label for label in dependency_labels if label not in known_batch_labels
        ]
        derived_soft_dependencies = {
            dependency
            for label in labels
            for dependency in soft_dependencies.get(label, set())
            if dependency and dependency not in label_set
        }
        if not derived_soft_dependencies:
            derived_soft_dependencies = {
                str(value)
                for value in (
                    source_batch.get(
                        "soft_dependency_labels",
                        previous.get("soft_dependency_labels", []),
                    )
                    or []
                )
                if str(value) and str(value) not in label_set
            }
        soft_dependency_labels = sorted(
            derived_soft_dependencies,
            key=lambda label: positions.get(label, len(positions) + 1),
        )
        attempts = []
        for raw_attempt in previous.get("attempts", []) or []:
            attempt = dict(raw_attempt) if isinstance(raw_attempt, Mapping) else raw_attempt
            # Native finalization and the campaign subprocess can observe the
            # same terminal outcome concurrently. Keep the ledger append-only
            # for genuine retries, but collapse byte-equivalent timestamped
            # deliveries of one outcome.
            if isinstance(attempt, Mapping) and attempt.get("recorded_at") and attempt in attempts:
                continue
            attempts.append(attempt)
        successful_stages = _successful_stages(attempts)
        agent_stages = _successful_stages(attempts, provenance="agent")
        manual_stages = _successful_stages(attempts, provenance="manual_gold")
        # Reconciliation deliberately invalidates a historical proof receipt
        # without deleting its append-only attempts.  Keep that invalidation
        # durable across campaign rebuilds until a *new* successful proof
        # attempt is appended (or the outcome recorder explicitly clears it).
        # ``attempt_count`` lets this work even for legacy attempts without
        # timestamps; attempts are append-only, so an index at/after the
        # reconciliation boundary is unambiguously new work.
        stale_proof_receipt = previous.get("stale_proof_receipt")
        if not isinstance(stale_proof_receipt, Mapping):
            stale_proof_receipt = source_batch.get("stale_proof_receipt")
        stale_proof_receipt = (
            dict(stale_proof_receipt) if isinstance(stale_proof_receipt, Mapping) else None
        )
        if stale_proof_receipt:
            try:
                attempt_boundary = int(stale_proof_receipt.get("attempt_count", len(attempts)))
            except (TypeError, ValueError):
                attempt_boundary = len(attempts)
            newly_successful_proof = any(
                isinstance(attempt, Mapping)
                and index >= attempt_boundary
                and str(attempt.get("stage", "proofs") or "proofs") == "proofs"
                and bool(attempt.get("success", False))
                for index, attempt in enumerate(attempts)
            )
            if newly_successful_proof:
                stale_proof_receipt = None
        # ``status`` and ``agent_status`` are recomputed from the append-only
        # ledger, so a terminal skip has to be re-derived here too or the next
        # rebuild would resurrect an unformalizable batch as pending.
        terminally_skipped = bool(attempts) and _attempt_is_terminal_skip(attempts[-1])
        escalation_requested = (
            bool(attempts)
            and isinstance(attempts[-1], Mapping)
            and not bool(attempts[-1].get("success", False))
            and bool(attempts[-1].get("escalate", False))
        )
        status = str(previous.get("status", "pending") or "pending")
        if unresolved_dependency_labels:
            # An unresolved hard edge invalidates even a historical receipt: a
            # completed artifact cannot establish semantic readiness for an
            # omitted predecessor.  Keep it visibly blocked until the graph is
            # repaired rather than letting the receipt mask the hole.
            status = "blocked_unresolved_dependency"
        elif stale_proof_receipt:
            status = "proof_retry"
        elif successful_stages:
            status = _status_from_stages(successful_stages)
        elif terminally_skipped:
            status = "skipped"
        elif escalation_requested:
            status = ESCALATION_STATUS
        completion_provenance = "none"
        if stale_proof_receipt:
            # The historical proof is no longer authoritative.  Preserve a
            # statement-only provenance hint when one exists, but never count
            # the batch as an end-to-end completed proof while it is retryable.
            if "statements" in agent_stages or "statements" in manual_stages:
                completion_provenance = "mixed_or_partial"
        elif "proofs" in agent_stages and "statements" in agent_stages:
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
                "source_file": str(
                    source_batch.get("source_file", previous.get("source_file", "")) or ""
                ),
                "dependency_labels": dependency_labels,
                "unresolved_dependency_labels": unresolved_dependency_labels,
                "soft_dependency_labels": soft_dependency_labels,
                "count": len(labels),
                "source_complexity_score": source_complexity_score,
                "source_complexity_tier": (
                    "complex"
                    if source_complexity_score >= 8
                    else "moderate" if source_complexity_score >= 4 else "routine"
                ),
                "source_subpart_count": source_subpart_count,
                "status": status,
                "agent_status": (
                    "skipped"
                    if terminally_skipped and not agent_stages
                    else (
                        "blocked_unresolved_dependency"
                        if unresolved_dependency_labels
                        else (
                            "proof_retry"
                            if stale_proof_receipt
                            else _status_from_stages(agent_stages)
                        )
                    )
                ),
                "completion_provenance": completion_provenance,
                "attempts": attempts,
                "last_outcome": dict(previous.get("last_outcome", {}) or {}),
                **({"stale_proof_receipt": stale_proof_receipt} if stale_proof_receipt else {}),
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
        # ``*_completed`` above is a stage/accounting count.  Keep a separate
        # integration count because a generated module can compile in isolation
        # while not being reachable from the project's public root import DAG.
        # Missing metadata is deliberately not treated as integrated, preserving
        # an honest count for legacy receipts.
        "root_reachable_statement_completed_batch_count": sum(
            batch["status"] in _STATEMENT_COMPLETE_STATUSES and _outcome_root_reachable(batch)
            for batch in batches
        ),
        "root_reachable_completed_batch_count": sum(
            batch["status"] in _TERMINAL_BATCH_STATUSES and _outcome_root_reachable(batch)
            for batch in batches
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
    campaign: Mapping[str, Any],
    *,
    stage: str = "statements",
    max_stage_attempts: int | None = None,
    allowed_complexity_tiers: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    """Return the next agent-lane batch independently of paid-action admission.

    ``status`` tracks whether any verified artifact exists, including manual gold.
    Scheduling instead follows ``agent_status`` so a gold proof never masquerades as
    an E2E agent completion or suppresses a future clean-room regression run.
    """
    eligible = (
        {"pending", "retry", "statement_retry", ESCALATION_STATUS}
        if stage == "statements"
        else {"statements_completed", "proof_retry"}
    )
    if stage not in {"statements", "proofs"}:
        raise ValueError(f"unknown campaign stage: {stage}")
    batches = [batch for batch in campaign.get("batches", []) or [] if isinstance(batch, Mapping)]
    # Legacy campaign ledgers may contain duplicate IDs.  Treat those IDs as
    # unsafe for automatic scheduling: selecting either row would make outcome
    # attribution ambiguous and could run the same batch concurrently.
    id_counts: dict[str, int] = {}
    for batch in batches:
        identifier = str(batch.get("id", "") or "").strip()
        if identifier:
            id_counts[identifier] = id_counts.get(identifier, 0) + 1
    duplicate_ids = {identifier for identifier, count in id_counts.items() if count > 1}
    label_statuses = {
        str(label): str(batch.get("agent_status", batch.get("status", "pending")))
        for batch in batches
        for label in batch.get("labels", []) or []
    }
    frontier = [
        batch
        for batch in batches
        if (
            str(batch.get("id", "") or "").strip() not in duplicate_ids
            and batch.get("agent_status", batch.get("status")) in eligible
            and not _lease_is_active(batch)
            and _batch_dependencies_ready(batch, stage=stage, label_statuses=label_statuses)
        )
    ]
    allowed_tiers = {
        str(tier or "").strip() for tier in (allowed_complexity_tiers or []) if str(tier).strip()
    }
    if allowed_tiers:
        frontier = [
            batch
            for batch in frontier
            if str(batch.get("source_complexity_tier", "routine") or "routine") in allowed_tiers
        ]
    if max_stage_attempts is not None:
        frontier = [
            batch
            for batch in frontier
            if batch_stage_attempt_count(batch, stage=stage) <= max_stage_attempts
        ]
    selected = _select_economic_frontier(
        frontier,
        stage=stage,
        label_statuses=label_statuses,
    )
    return dict(selected) if selected is not None else None


def lease_campaign_batches(
    campaign: Mapping[str, Any],
    *,
    stage: str,
    worker_ids: Sequence[str],
    ttl_seconds: int = 7200,
    now: datetime | None = None,
    reserve_usd: float | None = None,
    max_stage_attempts: int | None = None,
    allowed_complexity_tiers: Sequence[str] | None = None,
    batch_id: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Lease distinct eligible batches without treating them as completed work.

    ``batch_id`` is an optional exact selector used by operator-controlled
    pilots.  It is applied *inside* the same eligibility/dependency/lease
    checks as normal scheduling, so selecting a batch cannot bypass campaign
    state guards or claim an already leased/ineligible batch.
    """
    if stage not in {"statements", "proofs"}:
        raise ValueError(f"unknown campaign stage: {stage}")
    moment = now or datetime.now(UTC)
    updated = {**campaign, "batches": [dict(item) for item in campaign.get("batches", []) or []]}
    leased: list[dict[str, Any]] = []
    eligible = (
        {"pending", "retry", "statement_retry", ESCALATION_STATUS}
        if stage == "statements"
        else {"statements_completed", "proof_retry"}
    )
    label_statuses = {
        str(label): str(batch.get("agent_status", batch.get("status", "pending")))
        for batch in updated["batches"]
        for label in batch.get("labels", []) or []
    }
    allowed_tiers = {
        str(tier or "").strip() for tier in (allowed_complexity_tiers or []) if str(tier).strip()
    }
    requested_batch_id = str(batch_id or "").strip()
    id_counts: dict[str, int] = {}
    for batch in updated["batches"]:
        identifier = str(batch.get("id", "") or "").strip()
        if identifier:
            id_counts[identifier] = id_counts.get(identifier, 0) + 1
    duplicate_ids = {identifier for identifier, count in id_counts.items() if count > 1}
    if requested_batch_id and requested_batch_id in duplicate_ids:
        raise ValueError(f"campaign batch id is not unique: {requested_batch_id}")
    claimed_ids: set[str] = set()
    for worker_id in worker_ids:
        frontier = [
            batch
            for batch in updated["batches"]
            if str(batch.get("id", "") or "").strip() not in duplicate_ids
            and str(batch.get("id", "") or "").strip() not in claimed_ids
            and batch.get("agent_status", batch.get("status")) in eligible
            and not _lease_is_active(batch, now=moment)
            and _batch_dependencies_ready(batch, stage=stage, label_statuses=label_statuses)
            and (not requested_batch_id or str(batch.get("id", "")) == requested_batch_id)
        ]
        if allowed_tiers:
            frontier = [
                batch
                for batch in frontier
                if str(batch.get("source_complexity_tier", "routine") or "routine") in allowed_tiers
            ]
        if max_stage_attempts is not None:
            frontier = [
                batch
                for batch in frontier
                if batch_stage_attempt_count(batch, stage=stage) <= max_stage_attempts
            ]
        selected = _select_economic_frontier(
            frontier,
            stage=stage,
            label_statuses=label_statuses,
            allow_unready_soft=not leased,
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
        if reserve_usd is not None:
            # Persist the budget ceiling with the lease so another supervisor
            # can account for this in-flight action before admitting work.
            lease["reserve_usd"] = max(0.0, float(reserve_usd))
        selected["lease"] = lease
        leased.append(dict(selected))
        claimed_ids.add(str(selected.get("id", "") or "").strip())
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
    matching_count = sum(
        1
        for batch in updated["batches"]
        if str(batch.get("id", "") or "") == batch_id
    )
    if matching_count > 1:
        raise ValueError(f"campaign batch id is not unique: {batch_id}")
    for batch in updated["batches"]:
        if str(batch.get("id", "")) != batch_id:
            continue
        matched = True
        attempt = dict(outcome)
        # Native workers may finish after their lease expired or after another
        # worker reclaimed the batch.  Their snapshot is stale and must not
        # overwrite the newer status/outcome.  Legacy callers without a worker
        # identity retain the historical pure-transform behavior.
        worker_id = str(attempt.get("worker_id", "") or "").strip()
        if worker_id:
            lease = batch.get("lease")
            lease_owner = (
                str(lease.get("worker_id", "") or "").strip() if isinstance(lease, Mapping) else ""
            )
            if lease_owner != worker_id:
                # Preserve the current ledger verbatim.  Rebuilding the campaign
                # here can recompute statuses from a stale worker's synthetic
                # snapshot and accidentally undo a newer lease or outcome.
                return updated
        failure_class = classify_campaign_failure(attempt)
        if failure_class:
            attempt["failure_class"] = failure_class
        retry_class = classify_campaign_retry_class(attempt)
        if retry_class:
            attempt["retry_class"] = retry_class
        attempts = list(batch.get("attempts", []) or [])
        if not (attempt.get("recorded_at") and attempt in attempts):
            attempts.append(attempt)
        batch["attempts"] = attempts
        batch["last_outcome"] = attempt
        batch.pop("lease", None)
        stage = str(attempt.get("stage", "proofs") or "proofs")
        if stage not in {"statements", "proofs"}:
            raise ValueError(f"unknown campaign outcome stage: {stage}")
        success = bool(attempt.get("success", False))
        if success and stage == "proofs":
            # A fresh proof receipt supersedes a prior reconciliation marker.
            # Keep the historical attempts intact; only remove the derived
            # invalidation metadata.
            batch.pop("stale_proof_receipt", None)
        if not success:
            # A batch that keeps failing the same stage must stop being rescheduled:
            # otherwise it burns the wave budget on attempts that cannot converge and
            # holds every downstream batch at pending forever. Mark the *attempt*
            # terminal, not just the batch status, because ``build_campaign`` recomputes
            # status from the append-only ledger and would otherwise resurrect it.
            # Infrastructure failures are recorded for auditability and may be
            # retried, but they do not consume the semantic retry budget. This
            # prevents a provider outage from masquerading as a bad statement.
            stage_failures = sum(
                1
                for item in attempts
                if isinstance(item, Mapping)
                and str(item.get("stage", "proofs") or "proofs") == stage
                and not bool(item.get("success", False))
                and classify_campaign_retry_class(item) == RETRY_CLASS_SEMANTIC
            )
            escalation_spent = _statement_escalation_spent(attempts)
            if bool(attempt.get("escalated", False)):
                escalation_receipts = sum(
                    1
                    for item in attempts
                    if isinstance(item, Mapping) and bool(item.get("escalated", False))
                )
                semantic_receipts = sum(
                    1
                    for item in attempts
                    if isinstance(item, Mapping)
                    and bool(item.get("escalated", False))
                    and classify_campaign_retry_class(item) == RETRY_CLASS_SEMANTIC
                )
                if retry_class == RETRY_CLASS_INFRASTRUCTURE:
                    try:
                        infrastructure_limit = max(
                            1,
                            int(
                                attempt.get(
                                    "max_infrastructure_retries", MAX_ESCALATION_ATTEMPTS
                                )
                                or MAX_ESCALATION_ATTEMPTS
                            ),
                        )
                    except (TypeError, ValueError):
                        infrastructure_limit = MAX_ESCALATION_ATTEMPTS
                    if escalation_receipts < infrastructure_limit:
                        # The unbounded lane did not get a semantic verdict.
                        # Keep the item in the escalation lane for a fresh
                        # provider/session boundary.
                        attempt["escalate"] = True
                    else:
                        attempt["terminal"] = True
                        attempt["failure_class"] = "escalation_infrastructure_limit"
                        attempt["retry_limit_exhausted"] = escalation_receipts
                else:
                    try:
                        semantic_limit = max(
                            1,
                            int(
                                attempt.get(
                                    "max_semantic_repairs", MAX_ESCALATION_SEMANTIC_REPAIRS
                                )
                                or MAX_ESCALATION_SEMANTIC_REPAIRS
                            ),
                        )
                    except (TypeError, ValueError):
                        semantic_limit = MAX_ESCALATION_SEMANTIC_REPAIRS
                    if semantic_receipts < semantic_limit:
                        # A semantic BLOCK gets another attempt only through a
                        # new process; the current session is always finished.
                        attempt["escalate"] = True
                    else:
                        # Never let a later scheduler invocation turn this into
                        # another same-session semantic repair loop.
                        attempt["terminal"] = True
                        attempt["semantic_retry_limit_exhausted"] = semantic_receipts
                batch["last_outcome"] = attempt
            elif stage_failures >= MAX_STAGE_FAILURES_BEFORE_TERMINAL:
                # The cap is the authoritative verdict: the per-attempt class only
                # describes why this one attempt failed, not why the batch is being
                # parked. Keep it for diagnostics rather than discarding it.
                if failure_class:
                    attempt["last_failure_class"] = failure_class
                attempt["failure_class"] = "retry_limit"
                attempt["retry_limit_exhausted"] = stage_failures
                if stage == "statements" and not escalation_spent:
                    # Route to the unbounded lane before giving up.
                    attempt["escalate"] = True
                else:
                    attempt["terminal"] = True
                batch["last_outcome"] = attempt

        if not success and bool(attempt.get("terminal", False)):
            # A deterministically unformalizable source entry can never succeed
            # on retry. Park it in a terminal status so it stops being selected
            # and stops blocking batches that declared it as a dependency.
            batch["status"] = "skipped"
        elif not success and bool(attempt.get("escalate", False)):
            batch["status"] = ESCALATION_STATUS
        elif stage == "statements":
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
