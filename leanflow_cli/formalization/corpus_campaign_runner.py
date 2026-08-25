"""Plan and execute one resumable action in a book formalization campaign."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import uuid
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.utils import atomic_json_write
from leanflow_cli.formalization.bounded_statement_refinement import (
    refine_campaign_statement_bounded,
)
from leanflow_cli.formalization.campaign_store import read_campaign, update_campaign_file
from leanflow_cli.formalization.corpus_campaign import (
    build_campaign,
    classify_campaign_failure,
    lease_campaign_batches,
    next_campaign_batch,
    record_campaign_outcome,
    release_campaign_lease,
)
from leanflow_cli.formalization.corpus_planning import source_formalization_complexity
from leanflow_cli.formalization.formalization_document_runner import (
    _approved_blueprint_statement_review_text,
)
from leanflow_cli.lean.lean_attempt_location import _multi_attempt_replacement_candidate
from leanflow_cli.lean.lean_parsing import _declaration_line_index_from_text
from leanflow_cli.workflows import decomposition_provenance
from leanflow_cli.workflows.verification_providers import (
    BLUEPRINT_VERIFICATION_TASK,
    run_model_verification_review,
)
from leanflow_cli.workflows.verification_review import (
    _verification_review_decision,
    _verification_review_findings,
    _verification_review_result_payload,
)


class CampaignExecutionBlocked(RuntimeError):
    """Report campaign state that cannot safely produce an executable action."""


def refresh_campaign_source_complexity(
    campaign_path: str | Path,
    *,
    project_root: str | Path,
) -> bool:
    """Attach deterministic QA source-shape hints to legacy campaign batches."""
    path = Path(campaign_path).expanduser().resolve()
    root = Path(project_root).expanduser().resolve()
    campaign = read_campaign(path)
    source = (root / str(campaign.get("source", "") or "")).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        return False
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if isinstance(payload, Mapping):
        records = payload.get("items", payload.get("questions", [])) or []
    else:
        records = payload
    if not isinstance(records, list):
        return False
    hints: dict[str, dict[str, int | str]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        label = str(record.get("label", "") or record.get("id", "") or "").strip()
        if not label:
            continue
        text = " ".join(
            str(record.get(key, "") or "")
            for key in ("title", "question", "statement", "answer", "solution")
        )
        hints[label] = source_formalization_complexity(text)
    if not hints:
        return False
    changed = False

    def commit(current: Mapping[str, Any]):
        nonlocal changed
        updated = {**current, "batches": [dict(item) for item in current.get("batches", []) or []]}
        for batch in updated["batches"]:
            batch_hints = [
                hints[label] for label in batch.get("labels", []) or [] if label in hints
            ]
            if not batch_hints:
                continue
            score = max(int(item["source_complexity_score"]) for item in batch_hints)
            subparts = sum(int(item["source_subpart_count"]) for item in batch_hints)
            tier_rank = {"routine": 0, "moderate": 1, "complex": 2}
            tier = max(
                (str(item["source_complexity_tier"]) for item in batch_hints),
                key=lambda value: tier_rank.get(value, 0),
            )
            values = {
                "source_complexity_score": score,
                "source_complexity_tier": tier,
                "source_subpart_count": subparts,
            }
            if any(batch.get(key) != value for key, value in values.items()):
                batch.update(values)
                changed = True
        return updated, None

    update_campaign_file(path, commit)
    return changed


@dataclass(frozen=True)
class CampaignAction:
    """Describe one deterministic workflow subprocess without launching it."""

    stage: str
    batch_id: str
    labels: tuple[str, ...]
    argv: tuple[str, ...]
    target_file: str = ""


@dataclass(frozen=True)
class CampaignModelPolicy:
    """Route routine stages cheaply and escalate only after concrete failures."""

    statement_model: str = ""
    proof_model: str = ""
    escalation_model: str = ""
    escalate_after_failures: int = 2


def select_campaign_model(
    campaign: Mapping[str, Any],
    action: CampaignAction,
    *,
    fallback_model: str,
    policy: CampaignModelPolicy | None,
) -> str:
    """Return the stage model, escalating from durable same-stage failures."""
    if policy is None:
        return fallback_model
    batch = next(
        (
            item
            for item in campaign.get("batches", []) or []
            if isinstance(item, Mapping) and str(item.get("id", "")) == action.batch_id
        ),
        {},
    )
    failures = sum(
        1
        for attempt in batch.get("attempts", []) or []
        if isinstance(attempt, Mapping)
        and str(attempt.get("stage", "proofs") or "proofs") == action.stage
        and not bool(attempt.get("success", False))
        and classify_campaign_failure(attempt)
        in {"statement_generation_incomplete", "proof_incomplete", "verification_timeout"}
        and "signal interrupt" not in str(attempt.get("reason", "") or "").lower()
    )
    if policy.escalation_model and failures >= max(1, policy.escalate_after_failures):
        return policy.escalation_model
    stage_model = policy.statement_model if action.stage == "statements" else policy.proof_model
    return stage_model or fallback_model


_STATEMENT_RISK_PATTERNS: tuple[tuple[str, str], ...] = (
    ("source_context_missing", r"source packet is incomplete|source_context"),
    ("measurability_integrability", r"measurab|integrab|bochner integral|genuine expectation"),
    ("extended_value_semantics", r"ennreal|ereal|extended[- ](?:real|value)|\binfinity\b"),
    (
        "source_domain_mismatch",
        r"not bidirectionally faithful|changes? the (?:data|domain|object)|source has",
    ),
    (
        "totalized_edge_case",
        r"division by zero|denominator zero|n\s*=\s*0|totalized|truncated natural",
    ),
    ("meta_proof_repair", r"repair|fixing the proof|auxiliary .* lemma|actual .* theorem"),
    (
        "statement_format",
        r"statement lane|forbidden statement-lane token|body .* exactly `by sorry`",
    ),
)


def classify_statement_semantic_risks(attempt: Mapping[str, Any]) -> set[str]:
    """Extract stable remediation buckets from bounded statement diagnostics."""
    if str(attempt.get("stage", "") or "") != "statements":
        return set()
    texts = [
        str(item.get("diagnostic", "") or "")
        for item in attempt.get("candidate_diagnostics", []) or []
        if isinstance(item, Mapping)
    ]
    texts.extend(str(attempt.get(key, "") or "") for key in ("final_diagnostic", "reason"))
    evidence = "\n".join(texts).lower()
    return {
        category
        for category, pattern in _STATEMENT_RISK_PATTERNS
        if re.search(pattern, evidence, flags=re.IGNORECASE)
    }


def _nearest_rank_percentile(values: Sequence[float], percentile: float) -> float | None:
    clean = sorted(float(value) for value in values if float(value) >= 0)
    if not clean:
        return None
    index = max(0, min(len(clean) - 1, int((len(clean) * percentile + 0.999999) // 1) - 1))
    return round(clean[index], 6)


def campaign_marginal_cost_report(
    campaign: Mapping[str, Any], *, recent_attempt_limit: int = 30
) -> dict[str, Any]:
    """Estimate current one-pass economics without letting early experiments dominate."""
    samples: list[dict[str, Any]] = []
    batches = [item for item in campaign.get("batches", []) or [] if isinstance(item, Mapping)]
    for batch in batches:
        tier = str(batch.get("source_complexity_tier", "routine") or "routine")
        for attempt in batch.get("attempts", []) or []:
            if not isinstance(attempt, Mapping):
                continue
            cost = float(attempt.get("cost_usd", 0.0) or 0.0)
            stage = str(attempt.get("stage", "proofs") or "proofs")
            if cost <= 0 or stage not in {"statements", "proofs"}:
                continue
            samples.append(
                {
                    "stage": stage,
                    "tier": tier,
                    "cost_usd": cost,
                    "success": bool(attempt.get("success", False)),
                    "recorded_at": str(attempt.get("recorded_at", "") or ""),
                }
            )
    samples.sort(key=lambda item: item["recorded_at"] or "0000", reverse=True)
    recent = samples[: max(1, int(recent_attempt_limit))]

    cohorts: dict[str, dict[str, Any]] = {}
    for stage in ("statements", "proofs"):
        for tier in ("routine", "moderate", "complex"):
            selected = [item for item in recent if item["stage"] == stage and item["tier"] == tier]
            if not selected:
                continue
            costs = [float(item["cost_usd"]) for item in selected]
            key = f"{stage}:{tier}"
            cohorts[key] = {
                "attempts": len(selected),
                "successes": sum(bool(item["success"]) for item in selected),
                "success_rate": round(
                    sum(bool(item["success"]) for item in selected) / len(selected), 4
                ),
                "median_cost_usd": _nearest_rank_percentile(costs, 0.5),
                "p75_cost_usd": _nearest_rank_percentile(costs, 0.75),
                "max_cost_usd": round(max(costs), 6),
            }

    stage_p75 = {
        stage: _nearest_rank_percentile(
            [float(item["cost_usd"]) for item in recent if item["stage"] == stage], 0.75
        )
        for stage in ("statements", "proofs")
    }
    # Forecast one successful pass per unfinished stage. This deliberately does
    # not pretend to predict retries; the p75 column is a conservative wave-sizing
    # input, while observed success rates expose where that assumption is weak.
    forecast = 0.0
    forecast_coverage = 0
    remaining_stage_actions = 0
    for batch in batches:
        status = str(batch.get("agent_status", batch.get("status", "pending")) or "pending")
        tier = str(batch.get("source_complexity_tier", "routine") or "routine")
        needed = []
        if status in {"pending", "retry", "statement_retry"}:
            needed.append("statements")
        if status not in {"proofs_completed", "completed"}:
            needed.append("proofs")
        for stage in needed:
            remaining_stage_actions += 1
            cohort = cohorts.get(f"{stage}:{tier}", {})
            estimate = cohort.get("p75_cost_usd", stage_p75.get(stage))
            if estimate is not None:
                forecast += float(estimate)
                forecast_coverage += 1
    return {
        "window_attempt_limit": max(1, int(recent_attempt_limit)),
        "observed_attempts": len(recent),
        "cohorts": cohorts,
        "stage_p75_cost_usd": stage_p75,
        "remaining_stage_actions": remaining_stage_actions,
        "forecast_covered_actions": forecast_coverage,
        "one_pass_p75_forecast_usd": round(forecast, 2) if forecast_coverage else None,
        "forecast_caveat": "one pass per unfinished stage; retries and unsampled cohorts are excluded",
    }


def campaign_economics_report(campaign: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize coverage lanes and empirical proof difficulty for automation."""
    lanes = {
        "fresh_statements": 0,
        "statement_retries": 0,
        "fresh_proofs": 0,
        "proof_retries": 0,
        "hard_proof_retries": 0,
    }
    ranked_costs: list[dict[str, Any]] = []
    statement_risk_counts: dict[str, int] = {}
    for raw_batch in campaign.get("batches", []) or []:
        if not isinstance(raw_batch, Mapping):
            continue
        batch = dict(raw_batch)
        status = str(batch.get("agent_status", batch.get("status", "pending")) or "pending")
        statement_attempts = [
            item
            for item in batch.get("attempts", []) or []
            if isinstance(item, Mapping)
            and str(item.get("stage", "proofs") or "proofs") == "statements"
        ]
        batch_risks = set().union(
            *(classify_statement_semantic_risks(item) for item in statement_attempts)
        )
        for risk in batch_risks:
            statement_risk_counts[risk] = statement_risk_counts.get(risk, 0) + 1
        proof_attempts = [
            item
            for item in batch.get("attempts", []) or []
            if isinstance(item, Mapping)
            and str(item.get("stage", "proofs") or "proofs") == "proofs"
        ]
        proof_cost = sum(float(item.get("cost_usd", 0.0) or 0.0) for item in proof_attempts)
        total_cost = proof_cost + sum(
            float(item.get("cost_usd", 0.0) or 0.0) for item in statement_attempts
        )
        if status in {"pending", "retry", "statement_retry"}:
            lane = "fresh_statements" if not statement_attempts else "statement_retries"
            lanes[lane] += 1
        elif status in {"statements_completed", "proof_retry"}:
            lane = "fresh_proofs" if not proof_attempts else "proof_retries"
            lanes[lane] += 1
            substantive_failures = sum(
                classify_campaign_failure(item) in {"proof_incomplete", "verification_timeout"}
                for item in proof_attempts
            )
            if substantive_failures >= 2 or proof_cost >= 2.0:
                lanes["hard_proof_retries"] += 1
        ranked_costs.append(
            {
                "batch_id": str(batch.get("id", "") or ""),
                "status": status,
                "cost_usd": round(total_cost, 6),
                "proof_attempts": len(proof_attempts),
            }
        )
    ranked_costs.sort(key=lambda item: (-float(item["cost_usd"]), item["batch_id"]))
    completed = int(campaign.get("agent_e2e_completed_batch_count", 0) or 0)
    if not completed:
        completed = int(campaign.get("completed_batch_count", 0) or 0)
    spent = float(campaign.get("spent_usd", 0.0) or 0.0)
    return {
        **lanes,
        "completed_batches": completed,
        "spent_usd": spent,
        "cost_per_completed_batch_usd": round(spent / completed, 6) if completed else None,
        "statement_risk_counts": dict(sorted(statement_risk_counts.items())),
        "marginal_cost": campaign_marginal_cost_report(campaign),
        "top_cost_batches": ranked_costs[:10],
    }


def _batch_target_file(batch: Mapping[str, Any]) -> str:
    """Return the statement stage's generated Lean target when durably recorded."""
    outcome = dict(batch.get("last_outcome", {}) or {})
    return str(outcome.get("target_file", "") or "").strip()


def plan_next_campaign_action(
    campaign: Mapping[str, Any],
    *,
    python_executable: str,
) -> CampaignAction | None:
    """Plan proof-first continuation so each approved batch closes before drafting more."""
    noncomplex_statement = next_campaign_batch(
        campaign,
        stage="statements",
        allowed_complexity_tiers=("routine", "moderate"),
    )
    complex_statement = next_campaign_batch(
        campaign,
        stage="statements",
        allowed_complexity_tiers=("complex",),
    )
    statement_batch = noncomplex_statement or complex_statement
    # A source foundation unlocks downstream book items and should be drafted as
    # soon as its own statement dependencies are ready.  Ordinary item drafts
    # retain proof-first behavior so the corpus does not accumulate sorries.
    foundation_statement = (
        statement_batch
        if statement_batch is not None
        and str(statement_batch.get("selection_kind", "") or "") == "document"
        else None
    )
    fresh_proof_batch = (
        None
        if foundation_statement is not None
        else next_campaign_batch(
            campaign,
            stage="proofs",
            max_stage_attempts=0,
            allowed_complexity_tiers=("routine", "moderate"),
        )
    )
    # Give every approved statement one cheap proof attempt, then continue
    # corpus coverage. A few difficult theorems must not starve the rest of the
    # book; their retries resume after the fresh statement frontier is empty.
    proof_batch = fresh_proof_batch
    if proof_batch is None and noncomplex_statement is None and foundation_statement is None:
        proof_batch = next_campaign_batch(
            campaign,
            stage="proofs",
            max_stage_attempts=0,
            allowed_complexity_tiers=("complex",),
        )
    if proof_batch is None and statement_batch is None and foundation_statement is None:
        proof_batch = next_campaign_batch(campaign, stage="proofs")
    if proof_batch is not None:
        target_file = _batch_target_file(proof_batch)
        if not target_file:
            raise CampaignExecutionBlocked(
                f"batch {proof_batch.get('id', '')} has approved statements but no target file"
            )
        return CampaignAction(
            stage="proofs",
            batch_id=str(proof_batch.get("id", "") or ""),
            labels=tuple(str(label) for label in proof_batch.get("labels", []) or []),
            target_file=target_file,
            argv=(
                python_executable,
                "-m",
                "leanflow_cli.main",
                "workflow",
                "prove",
                target_file,
            ),
        )

    statement_batch = foundation_statement or statement_batch
    if statement_batch is None:
        return None
    source = str(campaign.get("source", "") or "").strip()
    if not source:
        raise CampaignExecutionBlocked("campaign source is missing")
    batch_id = str(statement_batch.get("id", "") or "")
    labels = tuple(str(label) for label in statement_batch.get("labels", []) or [])
    selection_kind = str(statement_batch.get("selection_kind", "batch") or "batch")
    if selection_kind == "items":
        if not labels:
            raise CampaignExecutionBlocked(f"batch {batch_id} has no explicit item labels")
        selector = ("--qa-items", ",".join(labels))
    elif selection_kind == "batch":
        selector = ("--qa-batch", batch_id)
    elif selection_kind == "document":
        source = str(statement_batch.get("source_file", "") or "").strip()
        if not source:
            raise CampaignExecutionBlocked(f"document batch {batch_id} has no source file")
        selector = ()
    else:
        raise CampaignExecutionBlocked(f"unknown batch selection kind: {selection_kind}")
    return CampaignAction(
        stage="statements",
        batch_id=batch_id,
        labels=labels,
        argv=(
            python_executable,
            "-m",
            "leanflow_cli.main",
            "workflow",
            "formalize",
            source,
            *selector,
        ),
    )


def plan_campaign_batch_action(
    campaign: Mapping[str, Any],
    batch: Mapping[str, Any],
    *,
    stage: str,
    python_executable: str,
) -> CampaignAction:
    """Plan a previously selected batch, including one protected by a lease."""
    batch_id = str(batch.get("id", "") or "")
    labels = tuple(str(label) for label in batch.get("labels", []) or [])
    if stage == "proofs":
        target_file = _batch_target_file(batch)
        if not target_file:
            raise CampaignExecutionBlocked(
                f"batch {batch_id} has approved statements but no target file"
            )
        return CampaignAction(
            stage="proofs",
            batch_id=batch_id,
            labels=labels,
            target_file=target_file,
            argv=(
                python_executable,
                "-m",
                "leanflow_cli.main",
                "workflow",
                "prove",
                target_file,
            ),
        )
    if stage != "statements":
        raise CampaignExecutionBlocked(f"unknown campaign stage: {stage}")
    source = str(campaign.get("source", "") or "").strip()
    selection_kind = str(batch.get("selection_kind", "batch") or "batch")
    if selection_kind == "items":
        if not labels:
            raise CampaignExecutionBlocked(f"batch {batch_id} has no explicit item labels")
        selector = ("--qa-items", ",".join(labels))
    elif selection_kind == "batch":
        selector = ("--qa-batch", batch_id)
    elif selection_kind == "document":
        source = str(batch.get("source_file", "") or "").strip()
        if not source:
            raise CampaignExecutionBlocked(f"document batch {batch_id} has no source file")
        selector = ()
    else:
        raise CampaignExecutionBlocked(f"unknown batch selection kind: {selection_kind}")
    if not source:
        raise CampaignExecutionBlocked("campaign source is missing")
    return CampaignAction(
        stage="statements",
        batch_id=batch_id,
        labels=labels,
        argv=(
            python_executable,
            "-m",
            "leanflow_cli.main",
            "workflow",
            "formalize",
            source,
            *selector,
        ),
    )


def lease_next_campaign_actions(
    campaign_path: str | Path,
    *,
    worker_count: int,
    python_executable: str,
    reserve_usd: float,
    lease_ttl_seconds: int = 7200,
) -> list[tuple[str, CampaignAction]]:
    """Atomically reserve a proof-first wave while accounting for all reservations."""
    if worker_count <= 0:
        raise CampaignExecutionBlocked("worker count must be positive")

    manifest_path = Path(campaign_path).expanduser().resolve().with_name("book-manifest.json")
    corpus_plan = read_campaign(manifest_path) if manifest_path.is_file() else None

    def claim(current: Mapping[str, Any]):
        if corpus_plan is not None:
            current = build_campaign(corpus_plan, existing=current)
        budget = current.get("budget_usd")
        if budget is None:
            raise CampaignExecutionBlocked("campaign has no explicit budget")
        remaining = max(
            0.0,
            float(budget) - float(current.get("spent_usd", 0.0) or 0.0),
        )
        capacity = min(worker_count, int(remaining // reserve_usd))
        if capacity <= 0:
            raise CampaignExecutionBlocked(
                "remaining campaign budget does not cover one action reservation"
            )
        working: Mapping[str, Any] = current
        claimed: list[tuple[str, CampaignAction]] = []
        noncomplex_proof_ready = (
            next_campaign_batch(
                current,
                stage="proofs",
                allowed_complexity_tiers=("routine", "moderate"),
            )
            is not None
        )
        noncomplex_statement_ready = (
            next_campaign_batch(
                current,
                stage="statements",
                allowed_complexity_tiers=("routine", "moderate"),
            )
            is not None
        )
        lanes: list[tuple[str, int | None, tuple[str, ...] | None]] = [
            ("proofs", 0, ("routine", "moderate")),
            ("statements", None, ("routine", "moderate")),
        ]
        if not noncomplex_statement_ready and not noncomplex_proof_ready:
            lanes.append(("proofs", 0, ("complex",)))
            lanes.append(("statements", None, ("complex",)))
            lanes.append(("proofs", None, ("complex",)))
        lanes.append(("proofs", None, ("routine", "moderate")))
        for stage, max_stage_attempts, allowed_complexity_tiers in lanes:
            open_slots = capacity - len(claimed)
            if open_slots <= 0:
                break
            worker_ids = [f"campaign-{uuid.uuid4().hex}" for _ in range(open_slots)]
            working, leased = lease_campaign_batches(
                working,
                stage=stage,
                worker_ids=worker_ids,
                ttl_seconds=lease_ttl_seconds,
                max_stage_attempts=max_stage_attempts,
                allowed_complexity_tiers=allowed_complexity_tiers,
            )
            for worker_id, batch in zip(worker_ids, leased, strict=False):
                claimed.append(
                    (
                        worker_id,
                        plan_campaign_batch_action(
                            working,
                            batch,
                            stage=stage,
                            python_executable=python_executable,
                        ),
                    )
                )
        return working, claimed

    return update_campaign_file(campaign_path, claim)


def campaign_execution_admitted(
    campaign: Mapping[str, Any],
    *,
    reserve_usd: float | None,
) -> tuple[bool, str]:
    """Admit a paid action only when an explicit budget covers its reservation."""
    budget = campaign.get("budget_usd")
    if budget is None:
        return False, "campaign has no explicit budget"
    if reserve_usd is None or reserve_usd <= 0:
        return False, "a positive per-action cost reservation is required"
    spent = float(campaign.get("spent_usd", 0.0) or 0.0)
    if spent + reserve_usd > float(budget):
        return False, "remaining campaign budget does not cover the action reservation"
    return True, "admitted"


def validate_campaign_action_paths(
    action: CampaignAction,
    *,
    project_root: str | Path,
    source_extensions: Sequence[str] = (".json", ".pdf", ".tex"),
) -> None:
    """Reject actions whose source or target escapes the registered Lean project."""
    root = Path(project_root).expanduser().resolve()
    if action.stage == "proofs":
        selected = action.target_file
    else:
        try:
            formalize_index = action.argv.index("formalize")
            selected = action.argv[formalize_index + 1]
        except (ValueError, IndexError) as exc:
            raise CampaignExecutionBlocked("formalization action has no source path") from exc
    path = (root / selected).resolve()
    if not path.is_relative_to(root):
        raise CampaignExecutionBlocked("campaign action path escapes the project")
    if action.stage == "statements" and path.suffix.lower() not in source_extensions:
        raise CampaignExecutionBlocked("formalization source has an unsupported extension")
    if action.stage == "proofs" and path.suffix.lower() != ".lean":
        raise CampaignExecutionBlocked("proof target is not a Lean file")


def _zero_cost_proof(source: str) -> str:
    local_defs = tuple(
        dict.fromkeys(re.findall(r"(?m)^\s*(?:def|abbrev)\s+([A-Za-z_][A-Za-z0-9_']*)\b", source))
    )
    branches = [
        "(rfl; done)",
        "(assumption; done)",
        "(simp; done)",
        "(norm_num; done)",
        "(omega; done)",
        "(linarith; done)",
        "(nlinarith; done)",
        "(ring; done)",
        "(aesop (config := { maxRuleApplications := 100 }); done)",
    ]
    if local_defs:
        definitions = ", ".join(local_defs)
        branches.extend(
            [
                f"(simp_all [{definitions}]; done)",
                (
                    f"(simp_all [{definitions}] <;> "
                    "aesop (config := { maxRuleApplications := 100 }); done)"
                ),
            ]
        )
    return "by\n  first | " + " | ".join(branches)


def try_zero_cost_proof_preflight(
    campaign_path: str | Path,
    *,
    project_root: str | Path,
    action: CampaignAction,
    lake_executable: str = "lake",
    timeout_s: int = 30,
    failure_diagnostics: list[str] | None = None,
) -> dict[str, Any] | None:
    """Close mechanically trivial approved goals before launching a paid prover."""
    if action.stage != "proofs" or not action.target_file:
        return None
    root = Path(project_root).expanduser().resolve()
    target = (root / action.target_file).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    source = target.read_text(encoding="utf-8")
    candidate_source, replacements = re.subn(
        r"\bby\s+sorry\b", lambda _match: _zero_cost_proof(source), source
    )
    if replacements <= 0 or re.search(r"\bby\s+sorry\b", candidate_source):
        return None
    candidate = target.with_name(f"ZeroCostCandidate_{uuid.uuid4().hex}.lean")
    candidate.write_text(candidate_source, encoding="utf-8")
    try:
        completed = subprocess.run(
            [lake_executable, "env", "lean", str(candidate)],
            cwd=str(root),
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout_s)),
            check=False,
        )
        if completed.returncode != 0:
            if failure_diagnostics is not None:
                failure_diagnostics.append((completed.stderr or completed.stdout or "")[-4000:])
            return None
        target.write_text(candidate_source, encoding="utf-8")
    except (OSError, subprocess.TimeoutExpired) as exc:
        if failure_diagnostics is not None:
            failure_diagnostics.append(str(exc))
        return None
    finally:
        candidate.unlink(missing_ok=True)

    outcome = {
        "stage": "proofs",
        "success": True,
        "exit_code": 0,
        "recorded_at": datetime.now(UTC).isoformat(),
        "target_file": action.target_file,
        "proof_obligations": 0,
        "reason": "zero-cost deterministic tactic preflight",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "cost_usd": 0.0,
        "cost_source": "none",
        "cost_scope": "local_lean_preflight",
        "provenance": "agent",
    }

    def commit(current: Mapping[str, Any]):
        updated = record_campaign_outcome(current, batch_id=action.batch_id, outcome=outcome)
        return updated, None

    update_campaign_file(campaign_path, commit)
    return outcome


def describe_next_campaign_action(
    campaign: Mapping[str, Any],
    *,
    python_executable: str,
    reserve_usd: float | None = None,
) -> dict[str, Any]:
    """Return a JSON-ready dry-run summary of progress and the next action."""
    action = plan_next_campaign_action(campaign, python_executable=python_executable)
    admitted, admission_reason = campaign_execution_admitted(campaign, reserve_usd=reserve_usd)
    return {
        "status": str(campaign.get("status", "") or ""),
        "batch_count": int(campaign.get("batch_count", 0) or 0),
        "statement_completed_batch_count": int(
            campaign.get("statement_completed_batch_count", 0) or 0
        ),
        "completed_batch_count": int(campaign.get("completed_batch_count", 0) or 0),
        "agent_e2e_completed_batch_count": int(
            campaign.get("agent_e2e_completed_batch_count", 0) or 0
        ),
        "manual_gold_completed_batch_count": int(
            campaign.get("manual_gold_completed_batch_count", 0) or 0
        ),
        "failure_class_counts": dict(campaign.get("failure_class_counts", {}) or {}),
        "economics": campaign_economics_report(campaign),
        "spent_usd": float(campaign.get("spent_usd", 0.0) or 0.0),
        "budget_usd": campaign.get("budget_usd"),
        "execution_admitted": admitted,
        "admission_reason": admission_reason,
        "next_action": (
            {
                "stage": action.stage,
                "batch_id": action.batch_id,
                "labels": list(action.labels),
                "target_file": action.target_file,
                "argv": list(action.argv),
            }
            if action is not None
            else None
        ),
    }


def execute_next_campaign_action(
    campaign_path: str | Path,
    *,
    project_root: str | Path,
    python_executable: str,
    reserve_usd: float,
    provider: str = "",
    model: str = "",
    statement_provider: str = "",
    statement_planner_provider: str = "",
    statement_planner_model: str = "",
    statement_fallback_provider: str = "",
    statement_fallback_model: str = "",
    statement_judge_provider: str = "",
    statement_judge_model: str = "",
    statement_candidates: int = 1,
    statement_candidate_workers: int = 4,
    model_policy: CampaignModelPolicy | None = None,
    environ: Mapping[str, str] | None = None,
    bounded_statements: bool = False,
    lake_executable: str = "lake",
) -> dict[str, Any]:
    """Execute exactly one admitted action; the native runner commits its outcome."""
    path = Path(campaign_path).expanduser().resolve()
    manifest_path = path.with_name("book-manifest.json")

    def refresh(current: Mapping[str, Any]):
        updated = (
            build_campaign(read_campaign(manifest_path), existing=current)
            if manifest_path.is_file()
            else dict(current)
        )
        return updated, updated

    campaign = update_campaign_file(path, refresh)
    action = plan_next_campaign_action(campaign, python_executable=python_executable)
    if action is None:
        return {"executed": False, "reason": "campaign has no remaining action"}
    validate_campaign_action_paths(action, project_root=project_root)
    admitted, reason = campaign_execution_admitted(campaign, reserve_usd=reserve_usd)
    if not admitted:
        raise CampaignExecutionBlocked(reason)
    return _execute_campaign_action(
        action,
        campaign_path=path,
        campaign=campaign,
        project_root=project_root,
        reserve_usd=reserve_usd,
        provider=provider,
        model=select_campaign_model(campaign, action, fallback_model=model, policy=model_policy),
        statement_provider=statement_provider,
        statement_planner_provider=statement_planner_provider,
        statement_planner_model=statement_planner_model,
        statement_fallback_provider=statement_fallback_provider,
        statement_fallback_model=statement_fallback_model,
        statement_judge_provider=statement_judge_provider,
        statement_judge_model=statement_judge_model,
        statement_candidates=statement_candidates,
        statement_candidate_workers=statement_candidate_workers,
        environ=environ,
        bounded_statements=bounded_statements,
        lake_executable=lake_executable,
    )


def _execute_campaign_action(
    action: CampaignAction,
    *,
    campaign_path: str | Path,
    campaign: Mapping[str, Any],
    project_root: str | Path,
    reserve_usd: float,
    provider: str = "",
    model: str = "",
    statement_provider: str = "",
    statement_planner_provider: str = "",
    statement_planner_model: str = "",
    statement_fallback_provider: str = "",
    statement_fallback_model: str = "",
    statement_judge_provider: str = "",
    statement_judge_model: str = "",
    statement_candidates: int = 1,
    statement_candidate_workers: int = 4,
    environ: Mapping[str, str] | None = None,
    bounded_statements: bool = False,
    lake_executable: str = "lake",
) -> dict[str, Any]:
    """Launch one already selected action without re-running global selection."""
    path = Path(campaign_path).expanduser().resolve()
    validate_campaign_action_paths(action, project_root=project_root)
    zero_cost_outcome = try_zero_cost_proof_preflight(
        path,
        project_root=project_root,
        action=action,
        lake_executable=lake_executable,
    )
    if zero_cost_outcome is not None:
        return {
            "executed": True,
            "stage": action.stage,
            "batch_id": action.batch_id,
            "exit_code": 0,
            "success": True,
            "outcome": zero_cost_outcome,
        }
    child_env = dict(environ or os.environ)
    plan_state_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", action.batch_id).strip("-_") or "batch"
    child_env.update(
        {
            "LEANFLOW_FORMALIZATION_CAMPAIGN": str(path),
            "LEANFLOW_FORMALIZATION_QA_BATCH": action.batch_id,
            "LEANFLOW_FORMALIZATION_PROVENANCE": "agent",
            "LEANFLOW_DISABLE_SOLUTION_RESEARCH": "1",
            "LEANFLOW_CLEAN_ROOM_DENY_PATHS": "FateXWork/Gold",
            "LEANFLOW_CLEAN_ROOM_DENY_MODULE_PREFIXES": "FateXWork.Gold",
            # Campaign actions are batch jobs even when the campaign runner was
            # launched from a TTY.  Do not let the child inherit that TTY and
            # strand the campaign in the post-run chat prompt.
            "LEANFLOW_NATIVE_INTERACTIVE": "0",
            # A corpus worker should pay the cold Lean startup cost only after
            # it has produced a concrete candidate.  The first foreground
            # check still starts Lean and remains kernel authoritative.
            "LEANFLOW_DEFER_FIRST_QUEUE_WARMUP": "1",
            # Checked helpers are safety-critical paid-work artifacts. Give
            # every batch a stable durable queue even outside research mode,
            # and isolate parallel batches so their pending candidates cannot
            # preempt one another in a shared summary file.
            "LEANFLOW_PLAN_STATE": "1",
            "LEANFLOW_PLAN_STATE_DIR": str(
                Path(project_root).expanduser().resolve()
                / ".leanflow"
                / "campaign-plan-state"
                / plan_state_slug
            ),
            "LEANFLOW_ACTION_COST_LIMIT_USD": str(
                min(
                    float(reserve_usd),
                    max(
                        0.0,
                        float(campaign.get("budget_usd", 0.0) or 0.0)
                        - float(campaign.get("spent_usd", 0.0) or 0.0),
                    ),
                )
            ),
        }
    )
    # Campaign review calls are retryable infrastructure stages. A shorter
    # deadline prevents one stalled auxiliary reviewer from pinning a model
    # worker for the general interactive default of three minutes.
    child_env.setdefault("LEANFLOW_ADVISORY_VERIFICATION_TIMEOUT_S", "90")
    worker_id = str(child_env.get("LEANFLOW_CAMPAIGN_WORKER_ID", "") or "").strip()
    if worker_id:
        child_env["LEANFLOW_WORKFLOW_STATE_NAMESPACE"] = worker_id
    selected_batch = next(
        (
            item
            for item in campaign.get("batches", []) or []
            if isinstance(item, Mapping) and str(item.get("id", "")) == action.batch_id
        ),
        {},
    )
    if bounded_statements and action.stage == "statements":
        outcome = refine_campaign_statement_bounded(
            path,
            project_root=project_root,
            batch_id=action.batch_id,
            reserve_usd=reserve_usd,
            provider=provider or "auto",
            planner_provider=statement_planner_provider,
            generator_provider=statement_provider,
            generator_fallback_provider=statement_fallback_provider,
            generator_fallback_model=statement_fallback_model,
            planner_model=statement_planner_model,
            judge_provider=statement_judge_provider,
            generator_model=model,
            judge_model=statement_judge_model or model,
            candidates_per_iteration=statement_candidates,
            candidate_workers=statement_candidate_workers,
            lake_executable=lake_executable,
            max_iterations=3,
            timeout_s=int(child_env.get("LEANFLOW_ADVISORY_VERIFICATION_TIMEOUT_S", "90")),
        )
        return {
            "executed": True,
            "stage": action.stage,
            "batch_id": action.batch_id,
            "exit_code": int(outcome["exit_code"]),
            "success": bool(outcome["success"]),
            "outcome": outcome,
        }
    last_outcome = dict(selected_batch.get("last_outcome", {}) or {})
    if action.stage == "proofs" and any(
        isinstance(attempt, Mapping) and str(attempt.get("stage", "") or "") == "proofs"
        for attempt in selected_batch.get("attempts", []) or []
    ):
        target_path = (Path(project_root).expanduser().resolve() / action.target_file).resolve()
        declarations: list[str] = []
        if target_path.is_file():
            declarations = re.findall(
                r"(?m)^\s*(?:private\s+)?(?:theorem|lemma|def)\s+([A-Za-z0-9_'.]+)",
                target_path.read_text(encoding="utf-8"),
            )[:24]
        child_env["LEANFLOW_PROOF_RESUME_EVIDENCE"] = (
            "This is a paid campaign retry. Preserve and use the declarations already present in the "
            f"target file: {declarations or '[none]'}. Previous outcome: "
            f"{str(last_outcome.get('reason', '') or '[unspecified]')}. Do not repeat broad project search, "
            "lean_decompose_helpers, or lean_reasoning_help before executing at least one concrete, "
            "substantive lean_incremental_check that advances the next missing helper or the target."
        )
    review_evidence = str(last_outcome.get("review_evidence", "") or "").strip()
    if (
        action.stage == "statements"
        and str(last_outcome.get("review_decision", "") or "").upper() == "BLOCK"
        and review_evidence
    ):
        evidence_path = (Path(project_root).expanduser().resolve() / review_evidence).resolve()
        if (
            evidence_path.is_relative_to(Path(project_root).expanduser().resolve())
            and evidence_path.is_file()
        ):
            child_env["LEANFLOW_FORMALIZATION_REVIEW_EVIDENCE"] = str(evidence_path)
    action_argv = action.argv
    action_argv = action.argv
    if provider.strip():
        action_argv = (*action_argv[:4], "--provider", provider.strip(), *action_argv[4:])
    if model.strip():
        # The outer CLI owns --provider, while --model is parsed from the
        # selected workflow's remainder after the workflow name.
        action_argv = (*action_argv, "--model", model.strip())
    process = subprocess.Popen(
        action_argv,
        cwd=str(Path(project_root).expanduser().resolve()),
        env=child_env,
        stdin=subprocess.DEVNULL,
        start_new_session=(os.name == "posix"),
    )
    try:
        return_code = process.wait()
    except BaseException:
        if process.poll() is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
        raise
    return {
        "executed": True,
        "stage": action.stage,
        "batch_id": action.batch_id,
        "exit_code": int(return_code),
        "success": return_code == 0,
    }


def execute_campaign_wave(
    campaign_path: str | Path,
    *,
    project_root: str | Path,
    python_executable: str,
    worker_count: int,
    reserve_usd: float,
    wave_budget_usd: float | None = None,
    provider: str = "",
    model: str = "",
    statement_provider: str = "",
    statement_planner_provider: str = "",
    statement_planner_model: str = "",
    statement_fallback_provider: str = "",
    statement_fallback_model: str = "",
    statement_judge_provider: str = "",
    statement_judge_model: str = "",
    statement_candidates: int = 1,
    statement_candidate_workers: int = 4,
    model_policy: CampaignModelPolicy | None = None,
    environ: Mapping[str, str] | None = None,
    lease_ttl_seconds: int = 7200,
    bounded_statements: bool = False,
    lake_executable: str = "lake",
) -> list[dict[str, Any]]:
    """Run a budget-safe wave of distinct leased batches concurrently."""
    path = Path(campaign_path).expanduser().resolve()
    if wave_budget_usd is not None:
        if wave_budget_usd <= 0:
            raise CampaignExecutionBlocked("wave budget must be positive")
        # ``reserve_usd`` is historically a per-action ceiling.  A separate
        # wave ceiling makes concurrency safe without silently multiplying the
        # operator's intended total spend by the worker count.
        action_reserve_usd = min(float(reserve_usd), float(wave_budget_usd) / worker_count)
    else:
        action_reserve_usd = float(reserve_usd)
    claims = lease_next_campaign_actions(
        path,
        worker_count=worker_count,
        python_executable=python_executable,
        reserve_usd=action_reserve_usd,
        lease_ttl_seconds=lease_ttl_seconds,
    )
    if not claims:
        return []

    def run_claim(worker_id: str, action: CampaignAction) -> dict[str, Any]:
        snapshot = read_campaign(path)
        selected_model = select_campaign_model(
            snapshot, action, fallback_model=model, policy=model_policy
        )
        try:
            result = _execute_campaign_action(
                action,
                campaign_path=path,
                campaign=snapshot,
                project_root=project_root,
                reserve_usd=action_reserve_usd,
                provider=provider,
                model=selected_model,
                statement_provider=statement_provider,
                statement_planner_provider=statement_planner_provider,
                statement_planner_model=statement_planner_model,
                statement_fallback_provider=statement_fallback_provider,
                statement_fallback_model=statement_fallback_model,
                statement_judge_provider=statement_judge_provider,
                statement_judge_model=statement_judge_model,
                statement_candidates=statement_candidates,
                statement_candidate_workers=statement_candidate_workers,
                environ={**dict(environ or os.environ), "LEANFLOW_CAMPAIGN_WORKER_ID": worker_id},
                bounded_statements=bounded_statements,
                lake_executable=lake_executable,
            )
            result["model"] = selected_model
            return result
        finally:
            # A normal native finalization removes the lease as part of its ledger
            # transaction.  This is the crash-before-finalization fallback.
            def release(current: Mapping[str, Any]):
                try:
                    updated = release_campaign_lease(
                        current, batch_id=action.batch_id, worker_id=worker_id
                    )
                except ValueError:
                    updated = dict(current)
                return updated, None

            update_campaign_file(path, release)

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(
        max_workers=len(claims), thread_name_prefix="leanflow-campaign"
    ) as pool:
        futures = {
            pool.submit(run_claim, worker_id, action): (worker_id, action)
            for worker_id, action in claims
        }
        for future in as_completed(futures):
            worker_id, action = futures[future]
            try:
                result = future.result()
            except BaseException as exc:
                result = {
                    "executed": True,
                    "stage": action.stage,
                    "batch_id": action.batch_id,
                    "worker_id": worker_id,
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            else:
                result["worker_id"] = worker_id
            results.append(result)
    return results


def _accept_locally_verified_stage(
    campaign_path: str | Path,
    *,
    project_root: str | Path,
    batch_id: str,
    stage: str,
    target_file: str = "",
    lake_executable: str = "lake",
) -> dict[str, Any]:
    """Compile and record a statement or proof repaired outside a paid turn."""
    path = Path(campaign_path).expanduser().resolve()
    campaign = json.loads(path.read_text(encoding="utf-8"))
    batch = next(
        (item for item in campaign.get("batches", []) or [] if str(item.get("id", "")) == batch_id),
        None,
    )
    if not isinstance(batch, Mapping):
        raise CampaignExecutionBlocked(f"unknown campaign batch: {batch_id}")
    target_file = str(target_file or _batch_target_file(batch)).strip()
    if not target_file:
        raise CampaignExecutionBlocked(f"batch {batch_id} has no recorded target file")
    action = CampaignAction(
        stage="proofs", batch_id=batch_id, labels=(), argv=(), target_file=target_file
    )
    validate_campaign_action_paths(action, project_root=project_root)
    completed = subprocess.run(
        [lake_executable, "env", "lean", target_file],
        cwd=str(Path(project_root).expanduser().resolve()),
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "Lean verification failed").strip()
        raise CampaignExecutionBlocked(details[-2000:])
    proof_obligations = (completed.stdout + completed.stderr).count("declaration uses `sorry`")
    if stage == "proofs" and proof_obligations:
        raise CampaignExecutionBlocked(
            f"local proof verification still reports {proof_obligations} sorry declaration(s)"
        )
    outcome = {
        "stage": stage,
        "success": True,
        "exit_code": 0,
        "reason": f"locally verified {stage} repair",
        "target_file": target_file,
        "proof_obligations": proof_obligations,
        "cost_usd": 0.0,
        "cost_source": "local",
        "cost_scope": "no_provider_call",
        "provenance": "manual_gold",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    updated = record_campaign_outcome(campaign, batch_id=batch_id, outcome=outcome)
    atomic_json_write(path, updated)
    return outcome


def accept_locally_verified_statement(
    campaign_path: str | Path,
    *,
    project_root: str | Path,
    batch_id: str,
    target_file: str = "",
    lake_executable: str = "lake",
) -> dict[str, Any]:
    """Record a type-correct statement repair; ``sorry`` is permitted at this stage."""
    return _accept_locally_verified_stage(
        campaign_path,
        project_root=project_root,
        batch_id=batch_id,
        stage="statements",
        target_file=target_file,
        lake_executable=lake_executable,
    )


def accept_agent_reviewed_statement(
    campaign_path: str | Path,
    *,
    project_root: str | Path,
    batch_id: str,
    review_file: str | Path,
    target_file: str = "",
    lake_executable: str = "lake",
) -> dict[str, Any]:
    """Commit an independently reviewed agent draft without another paid turn.

    This transition is intentionally narrower than ``--accept-local-statement``:
    it requires durable PASS evidence and an approval stamp in the target's
    blueprint, then repeats the Lean kernel check before recording agent
    provenance.  It lets a campaign recover after finalization/budget failures
    without paying an LLM to repeat an already completed source review.
    """
    root = Path(project_root).expanduser().resolve()
    evidence = Path(review_file).expanduser().resolve()
    if not evidence.is_relative_to(root) or not evidence.is_file():
        raise CampaignExecutionBlocked("review evidence must be an existing project file")
    review_text = evidence.read_text(encoding="utf-8")
    if not re.search(r"(?im)^\s*(?:verdict\s*:\s*)?PASS\b", review_text):
        raise CampaignExecutionBlocked("independent review evidence does not record PASS")

    path = Path(campaign_path).expanduser().resolve()
    campaign = json.loads(path.read_text(encoding="utf-8"))
    batch = next(
        (item for item in campaign.get("batches", []) or [] if str(item.get("id", "")) == batch_id),
        None,
    )
    if not isinstance(batch, Mapping):
        raise CampaignExecutionBlocked(f"unknown campaign batch: {batch_id}")
    selected_target = str(target_file or _batch_target_file(batch)).strip()
    if not selected_target:
        raise CampaignExecutionBlocked(f"batch {batch_id} has no recorded target file")
    action = CampaignAction(
        stage="proofs", batch_id=batch_id, labels=(), argv=(), target_file=selected_target
    )
    validate_campaign_action_paths(action, project_root=root)
    blueprint = (root / selected_target).resolve().with_name("Blueprint.md")
    if not blueprint.is_file():
        raise CampaignExecutionBlocked("agent statement has no sibling Blueprint.md")
    blueprint_text = blueprint.read_text(encoding="utf-8")
    if not re.search(
        r"(?im)^\s*-\s*Statement verification status\s*:\s*.*\b(approved|verified|reviewed|accepted)\b",
        blueprint_text,
    ):
        raise CampaignExecutionBlocked(
            "blueprint does not contain an approved statement review stamp"
        )

    completed = subprocess.run(
        [lake_executable, "env", "lean", selected_target],
        cwd=str(root),
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "Lean verification failed").strip()
        raise CampaignExecutionBlocked(details[-2000:])
    proof_obligations = (completed.stdout + completed.stderr).count("declaration uses `sorry`")
    outcome = {
        "stage": "statements",
        "success": True,
        "exit_code": 0,
        "reason": "recovered independently reviewed agent statement handoff",
        "target_file": selected_target,
        "proof_obligations": proof_obligations,
        "cost_usd": 0.0,
        "cost_source": "review_reuse",
        "cost_scope": "no_provider_call",
        "provenance": "agent",
        "review_evidence": str(evidence.relative_to(root)),
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    updated = record_campaign_outcome(campaign, batch_id=batch_id, outcome=outcome)
    atomic_json_write(path, updated)
    return outcome


def review_existing_agent_statement(
    campaign_path: str | Path,
    *,
    project_root: str | Path,
    batch_id: str,
    reserve_usd: float,
    provider: str = "main",
    model: str = "",
    timeout_s: int = 90,
    target_file: str = "",
    lake_executable: str = "lake",
    project_build_target: str = "",
) -> dict[str, Any]:
    """Independently review and commit one existing agent statement draft.

    This is deliberately separate from the drafting conversation: retries send
    only the bounded source slice, blueprint, and generated Lean declarations,
    and reviewer usage is recorded as its own campaign attempt.
    """
    path = Path(campaign_path).expanduser().resolve()
    root = Path(project_root).expanduser().resolve()
    campaign = read_campaign(path)
    admitted, reason = campaign_execution_admitted(campaign, reserve_usd=reserve_usd)
    if not admitted:
        raise CampaignExecutionBlocked(reason)
    batch = next(
        (
            item
            for item in campaign.get("batches", []) or []
            if isinstance(item, Mapping) and str(item.get("id", "")) == batch_id
        ),
        None,
    )
    if not isinstance(batch, Mapping):
        raise CampaignExecutionBlocked(f"unknown campaign batch: {batch_id}")
    selected_target = str(target_file or _batch_target_file(batch)).strip()
    if not selected_target:
        raise CampaignExecutionBlocked(f"batch {batch_id} has no recorded target file")
    action = CampaignAction(
        stage="proofs", batch_id=batch_id, labels=(), argv=(), target_file=selected_target
    )
    validate_campaign_action_paths(action, project_root=root)
    target = (root / selected_target).resolve()
    blueprint = target.with_name("Blueprint.md")
    if not blueprint.is_file():
        raise CampaignExecutionBlocked("agent statement has no sibling Blueprint.md")
    source_candidates = list(
        (root / ".leanflow" / "workflow-state" / "formalization").glob(
            f"*/batches/{batch_id}/extracted.txt"
        )
    )
    if len(source_candidates) != 1:
        raise CampaignExecutionBlocked(
            f"expected one bounded extracted source for {batch_id}, found {len(source_candidates)}"
        )

    verification_commands = [
        [lake_executable, "env", "lean", selected_target],
        [lake_executable, "build", *([project_build_target] if project_build_target else [])],
    ]
    for command in verification_commands:
        completed = subprocess.run(
            command,
            cwd=str(root),
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout or "Lean verification failed").strip()
            raise CampaignExecutionBlocked(details[-3000:])

    source_text = source_candidates[0].read_text(encoding="utf-8")[:16000]
    blueprint_text = blueprint.read_text(encoding="utf-8")
    target_text = target.read_text(encoding="utf-8")
    prompt = (
        "Independently review this natural-language-to-Lean statement draft.\n\n"
        "Start with exactly PASS or BLOCK on its own line. PASS only if every source claim, "
        "quantifier, hypothesis, conclusion, sharpness/existence clause, and stated scope change "
        "is faithfully represented by the Lean declarations. Explicit additional integrability or "
        "representation assumptions are acceptable only when disclosed. Check the actual typeclass "
        "semantics of overloaded notation, especially norms, distances, scalar actions, division, "
        "square roots, and finite-dimensional space representations; compiling notation can denote "
        "the wrong mathematics. The `sorry` bodies are "
        "intentional and must not affect the statement verdict. Then give concise Findings and "
        "Correction steps. Do not edit files and do not claim the proofs are complete.\n\n"
        f"Batch: {batch_id}\nTarget: {selected_target}\n\n"
        f"SOURCE SLICE\n```text\n{source_text}\n```\n\n"
        f"BLUEPRINT\n```markdown\n{blueprint_text[:24000]}\n```\n\n"
        f"LEAN DECLARATIONS\n```lean\n{target_text[:24000]}\n```"
    )
    previous_model = os.environ.get("AUXILIARY_BLUEPRINT_VERIFICATION_MODEL")
    if model:
        os.environ["AUXILIARY_BLUEPRINT_VERIFICATION_MODEL"] = model
    try:
        result = run_model_verification_review(
            provider=provider,
            task=BLUEPRINT_VERIFICATION_TASK,
            prompt=prompt,
            system_prompt=(
                "You are a read-only mathematical formalization reviewer. Compare source meaning "
                "against Lean types exactly; never approve based only on compilation."
            ),
            timeout_s=max(5, min(300, int(timeout_s))),
            max_tokens=4000,
        )
    finally:
        if model:
            if previous_model is None:
                os.environ.pop("AUXILIARY_BLUEPRINT_VERIFICATION_MODEL", None)
            else:
                os.environ["AUXILIARY_BLUEPRINT_VERIFICATION_MODEL"] = previous_model
    payload = _verification_review_result_payload(result)
    decision = _verification_review_decision(payload)
    findings = _verification_review_findings(payload, limit=12)
    evidence = target.with_name("IndependentReview.md")
    evidence_text = (
        "# Independent statement/source review\n\n"
        f"Verdict: {decision or 'ERROR'}\n\n"
        f"Provider: `{payload.get('provider') or provider}`\n\n"
        f"Model: `{payload.get('model') or model or '[unknown]'}`\n\n"
        "Reviewer response:\n\n"
        f"{payload.get('response') or payload.get('error') or '[no response]'}\n"
    )
    evidence.write_text(evidence_text, encoding="utf-8")
    success = decision == "PASS" and str(payload.get("status", "")) == "ok"
    if success:
        approved, changed = _approved_blueprint_statement_review_text(
            blueprint_text, str(payload.get("provider") or provider)
        )
        if not changed:
            raise CampaignExecutionBlocked(
                "review passed but blueprint had no review stamp to apply"
            )
        blueprint.write_text(approved, encoding="utf-8")
    outcome = {
        "stage": "statements",
        "success": success,
        "exit_code": 0 if success else 2,
        "reason": (
            "independent bounded statement/source review passed"
            if success
            else "independent bounded statement/source review did not pass"
        ),
        "target_file": selected_target,
        "proof_obligations": target_text.count("sorry"),
        "cost_usd": float(payload.get("cost_usd", 0.0) or 0.0),
        "cost_source": "reviewer_token_usage" if payload.get("total_tokens") else "unavailable",
        "cost_scope": "independent_statement_reviewer",
        "provenance": "agent",
        "review_evidence": str(evidence.relative_to(root)),
        "review_decision": decision,
        "review_status": str(payload.get("status", "") or ""),
        "review_findings": findings,
        "model": str(payload.get("model", "") or model),
        "provider": str(payload.get("provider", "") or provider),
        "usage": {
            "prompt_tokens": int(payload.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(payload.get("completion_tokens", 0) or 0),
            "total_tokens": int(payload.get("total_tokens", 0) or 0),
        },
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }

    def commit(current: Mapping[str, Any]):
        return record_campaign_outcome(current, batch_id=batch_id, outcome=outcome), None

    update_campaign_file(path, commit)
    return outcome


def accept_locally_verified_proof(
    campaign_path: str | Path,
    *,
    project_root: str | Path,
    batch_id: str,
    target_file: str = "",
    lake_executable: str = "lake",
) -> dict[str, Any]:
    """Record a kernel-checked local proof, rejecting every remaining ``sorry``."""
    return _accept_locally_verified_stage(
        campaign_path,
        project_root=project_root,
        batch_id=batch_id,
        stage="proofs",
        target_file=target_file,
        lake_executable=lake_executable,
    )


def recover_agent_verified_proof(
    campaign_path: str | Path,
    *,
    project_root: str | Path,
    batch_id: str,
    lake_executable: str = "lake",
) -> dict[str, Any]:
    """Recover a durable exact LeanProbe candidate after budget/crash finalization."""
    root = Path(project_root).expanduser().resolve()
    path = Path(campaign_path).expanduser().resolve()
    campaign = json.loads(path.read_text(encoding="utf-8"))
    batch = next(
        (item for item in campaign.get("batches", []) or [] if str(item.get("id", "")) == batch_id),
        None,
    )
    if not isinstance(batch, Mapping):
        raise CampaignExecutionBlocked(f"unknown campaign batch: {batch_id}")
    target_file = str(_batch_target_file(batch)).strip()
    target = (root / target_file).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise CampaignExecutionBlocked("campaign target is missing or escapes the project")

    matches: list[tuple[str, dict[str, Any], Path]] = []
    outcome_roots = [root / ".leanflow" / "workflow-state" / "outcomes.jsonl"]
    outcome_roots.extend(
        (root / ".leanflow" / "workflow-state" / "workers").glob("*/outcomes.jsonl")
    )
    for evidence in outcome_roots:
        if not evidence.is_file():
            continue
        for line in evidence.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = dict(record.get("payload", {}) or {})
            candidate_file = Path(str(payload.get("file_path", "") or "")).expanduser()
            if not candidate_file.is_absolute():
                candidate_file = root / candidate_file
            verified = [
                str(item or "").strip()
                for item in payload.get("verified_attempts", []) or []
                if str(item or "").strip()
            ]
            if (
                str(record.get("kind", "") or "") == "lean-multi-attempt"
                and payload.get("target_verified") is True
                and candidate_file.resolve() == target
                and len(verified) == 1
            ):
                matches.append((str(record.get("timestamp", "") or ""), payload, evidence))
    before = target.read_bytes()
    source = before.decode("utf-8")
    target_declaration = next(
        (
            (str(item.get("name", "") or ""), str(item.get("text", "") or ""))
            for item in _declaration_line_index_from_text(source)
            if str(item.get("name", "") or "")
            in {str(value) for value in batch.get("declarations", []) or []}
            or (
                "sorry" in str(item.get("text", "") or "")
                and str(item.get("kind", "") or "") in {"theorem", "lemma"}
            )
        ),
        ("", ""),
    )
    declaration_name, old_declaration = target_declaration
    tactic = ""
    evidence: Path
    if matches:
        _timestamp, payload, evidence = max(matches, key=lambda item: item[0])
        try:
            line = int(payload.get("line", 0) or 0)
            raw_column = payload.get("column")
            column = int(raw_column) if raw_column not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise CampaignExecutionBlocked(
                "verified candidate has invalid source coordinates"
            ) from exc
        tactic = str(payload["verified_attempts"][0]).strip()
        replacement = _multi_attempt_replacement_candidate(target, line, column, tactic)
        if replacement is None:
            raise CampaignExecutionBlocked("verified candidate no longer matches current source")
        declaration_name, declaration = replacement
    else:
        evidence = root / ".leanflow" / "campaign-plan-state" / batch_id / "summary.json"
        try:
            summary = json.loads(evidence.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CampaignExecutionBlocked(
                "no durable exact LeanProbe candidate found for batch"
            ) from exc
        candidate = dict(summary.get("pending_research_helper_candidate", {}) or {})
        helper_declaration = str(candidate.get("declaration", "") or "").strip()
        if (
            candidate.get("state") != "ready_to_integrate"
            or candidate.get("parent_recheck_status") != "accepted"
            or Path(str(candidate.get("active_file", "") or "")).resolve() != target
            or str(candidate.get("target_symbol", "") or "") != declaration_name
            or not helper_declaration
        ):
            raise CampaignExecutionBlocked(
                "no parent-accepted durable helper candidate matches the target"
            )
        helper_name = str(candidate.get("helper_name", "") or "")
        helper_header = helper_declaration.partition(":=")[0]
        target_header = old_declaration.partition(":=")[0]

        def normalized_header(header: str, name: str) -> str:
            declaration_start = re.search(r"\b(?:theorem|lemma)\s+", header)
            scoped = header[declaration_start.start() :] if declaration_start else header
            scoped = re.sub(r"^private\s+", "", scoped.strip())
            scoped = re.sub(rf"\b{re.escape(name)}\b", "__TARGET__", scoped, count=1)
            return re.sub(r"\s+", " ", scoped).strip()

        if normalized_header(helper_header, helper_name) != normalized_header(
            target_header, declaration_name
        ):
            raise CampaignExecutionBlocked(
                "parent-accepted helper is not signature-equivalent to the assigned target"
            )
        separator = helper_declaration.find(":=")
        if separator < 0 or "sorry" in helper_declaration[separator:]:
            raise CampaignExecutionBlocked("durable helper proof is incomplete")
        declaration = old_declaration[: old_declaration.find(":=")] + helper_declaration[separator:]
        tactic = "parent-accepted signature-equivalent helper"
    if not old_declaration or source.count(old_declaration) != 1:
        raise CampaignExecutionBlocked("verified declaration cannot be uniquely recovered")
    after = source.replace(old_declaration, declaration, 1).encode("utf-8")
    if not decomposition_provenance.compare_and_swap_source(
        target, expected_bytes=before, replacement_bytes=after
    ):
        raise CampaignExecutionBlocked("target changed while recovering verified candidate")
    completed = subprocess.run(
        [lake_executable, "env", "lean", target_file],
        cwd=str(root),
        check=False,
        text=True,
        capture_output=True,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0 or "declaration uses `sorry`" in output:
        decomposition_provenance.compare_and_swap_source(
            target, expected_bytes=after, replacement_bytes=before
        )
        raise CampaignExecutionBlocked((output or "Lean verification failed")[-2000:])
    outcome = {
        "stage": "proofs",
        "success": True,
        "exit_code": 0,
        "reason": "recovered durable agent LeanProbe proof candidate",
        "target_file": target_file,
        "proof_obligations": 0,
        "cost_usd": 0.0,
        "cost_source": "durable_agent_evidence",
        "cost_scope": "no_additional_provider_call",
        "provenance": "agent",
        "recovery_evidence": str(evidence.relative_to(root)),
        "recovered_tactic": tactic,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    updated = record_campaign_outcome(campaign, batch_id=batch_id, outcome=outcome)
    atomic_json_write(path, updated)
    return outcome


def main(argv: Sequence[str] | None = None) -> int:
    """Inspect a campaign or explicitly execute one budget-admitted action."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--reserve-usd", type=float, default=None)
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--statement-model", default="")
    parser.add_argument("--statement-provider", default="")
    parser.add_argument("--statement-planner-provider", default="")
    parser.add_argument("--statement-planner-model", default="")
    parser.add_argument("--statement-fallback-provider", default="")
    parser.add_argument("--statement-fallback-model", default="")
    parser.add_argument("--statement-judge-provider", default="")
    parser.add_argument("--statement-judge-model", default="")
    parser.add_argument("--statement-candidates", type=int, default=1)
    parser.add_argument("--statement-candidate-workers", type=int, default=4)
    parser.add_argument("--proof-model", default="")
    parser.add_argument("--escalation-model", default="")
    parser.add_argument("--escalate-after-failures", type=int, default=2)
    parser.add_argument("--reasoning-effort", default="")
    parser.add_argument("--batch-item-limit", type=int, default=None)
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--bounded-statements", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--wave-budget-usd",
        type=float,
        default=None,
        help="total cost ceiling shared by concurrent workers (reserve-usd remains per action)",
    )
    parser.add_argument("--lean-slots", type=int, default=1)
    parser.add_argument("--lease-ttl-seconds", type=int, default=7200)
    parser.add_argument("--accept-local-statement", default="")
    parser.add_argument("--accept-agent-reviewed-statement", default="")
    parser.add_argument("--review-file", default="")
    parser.add_argument("--accept-local-proof", default="")
    parser.add_argument("--recover-agent-proof", default="")
    parser.add_argument("--review-agent-statement", default="")
    parser.add_argument("--refine-statement-bounded", default="")
    parser.add_argument("--max-statement-iterations", type=int, default=3)
    parser.add_argument("--review-provider", default="main")
    parser.add_argument("--review-model", default="")
    parser.add_argument("--review-timeout-seconds", type=int, default=90)
    parser.add_argument("--project-build-target", default="")
    parser.add_argument("--local-target", default="")
    parser.add_argument("--lake-executable", default="lake")
    args = parser.parse_args(list(argv) if argv is not None else None)
    campaign_path = Path(args.campaign).expanduser().resolve()
    project_root = Path(args.project_root).expanduser().resolve()
    refresh_campaign_source_complexity(campaign_path, project_root=project_root)
    if args.refine_statement_bounded:
        if args.reserve_usd is None or args.reserve_usd <= 0:
            parser.error("--refine-statement-bounded requires a positive --reserve-usd")
        if not 1 <= args.max_statement_iterations <= 3:
            parser.error("--max-statement-iterations must be between 1 and 3")
        if not 1 <= args.statement_candidates <= 8:
            parser.error("--statement-candidates must be between 1 and 8")
        outcome = refine_campaign_statement_bounded(
            campaign_path,
            project_root=project_root,
            batch_id=args.refine_statement_bounded,
            reserve_usd=args.reserve_usd,
            provider=args.review_provider,
            generator_provider=args.statement_provider,
            planner_provider=args.statement_planner_provider,
            generator_fallback_provider=args.statement_fallback_provider,
            generator_fallback_model=args.statement_fallback_model,
            planner_model=args.statement_planner_model,
            judge_provider=args.statement_judge_provider or args.review_provider,
            generator_model=args.statement_model or args.model,
            judge_model=args.statement_judge_model or args.review_model,
            lake_executable=args.lake_executable,
            max_iterations=args.max_statement_iterations,
            candidates_per_iteration=args.statement_candidates,
            candidate_workers=args.statement_candidate_workers,
            timeout_s=args.review_timeout_seconds,
        )
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0 if outcome["success"] else 1
    if args.review_agent_statement:
        if args.reserve_usd is None or args.reserve_usd <= 0:
            parser.error("--review-agent-statement requires a positive --reserve-usd")
        outcome = review_existing_agent_statement(
            campaign_path,
            project_root=project_root,
            batch_id=args.review_agent_statement,
            reserve_usd=args.reserve_usd,
            provider=args.review_provider,
            model=args.review_model,
            timeout_s=args.review_timeout_seconds,
            target_file=args.local_target,
            lake_executable=args.lake_executable,
            project_build_target=args.project_build_target,
        )
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0 if outcome["success"] else 1
    if args.accept_local_statement:
        outcome = accept_locally_verified_statement(
            campaign_path,
            project_root=project_root,
            batch_id=args.accept_local_statement,
            target_file=args.local_target,
            lake_executable=args.lake_executable,
        )
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0
    if args.accept_agent_reviewed_statement:
        if not args.review_file:
            parser.error("--accept-agent-reviewed-statement requires --review-file")
        outcome = accept_agent_reviewed_statement(
            campaign_path,
            project_root=project_root,
            batch_id=args.accept_agent_reviewed_statement,
            review_file=args.review_file,
            target_file=args.local_target,
            lake_executable=args.lake_executable,
        )
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0
    if args.accept_local_proof:
        outcome = accept_locally_verified_proof(
            campaign_path,
            project_root=project_root,
            batch_id=args.accept_local_proof,
            target_file=args.local_target,
            lake_executable=args.lake_executable,
        )
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0
    if args.recover_agent_proof:
        outcome = recover_agent_verified_proof(
            campaign_path,
            project_root=project_root,
            batch_id=args.recover_agent_proof,
            lake_executable=args.lake_executable,
        )
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0
    if args.batch_item_limit is not None or args.budget_usd is not None:
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        if not isinstance(campaign, dict):
            raise CampaignExecutionBlocked("campaign JSON must contain an object")
        if args.batch_item_limit is not None:
            if args.batch_item_limit <= 0:
                parser.error("--batch-item-limit must be positive")
            manifest_path = campaign_path.with_name("book-manifest.json")
            if not manifest_path.is_file():
                raise CampaignExecutionBlocked("book-manifest.json is required to repartition")
            corpus_plan = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(corpus_plan, dict):
                raise CampaignExecutionBlocked("book manifest JSON must contain an object")
            campaign = build_campaign(
                corpus_plan,
                existing={**campaign, "batch_item_limit": args.batch_item_limit},
            )
        if args.budget_usd is not None:
            if args.budget_usd < float(campaign.get("spent_usd", 0.0) or 0.0):
                parser.error("--budget-usd cannot be below already-spent campaign cost")
            campaign["budget_usd"] = args.budget_usd
        atomic_json_write(campaign_path, campaign)
    if args.execute:
        if args.reserve_usd is None:
            parser.error("--execute requires --reserve-usd")
        if args.workers <= 0:
            parser.error("--workers must be positive")
        if args.wave_budget_usd is not None and args.wave_budget_usd <= 0:
            parser.error("--wave-budget-usd must be positive")
        if not 1 <= args.lean_slots <= 8:
            parser.error("--lean-slots must be between 1 and 8")
        if not 1 <= args.statement_candidates <= 8:
            parser.error("--statement-candidates must be between 1 and 8")
        if args.statement_candidate_workers <= 0:
            parser.error("--statement-candidate-workers must be positive")
        execution_env = {
            **os.environ,
            "LEANFLOW_PROJECT_LEAN_CAPACITY": str(args.lean_slots),
        }
        if args.reasoning_effort:
            execution_env["LEANFLOW_CODEX_REASONING_EFFORT"] = args.reasoning_effort
        if args.escalate_after_failures <= 0:
            parser.error("--escalate-after-failures must be positive")
        model_policy = CampaignModelPolicy(
            statement_model=args.statement_model,
            proof_model=args.proof_model,
            escalation_model=args.escalation_model,
            escalate_after_failures=args.escalate_after_failures,
        )
        if args.workers == 1:
            outcome = execute_next_campaign_action(
                campaign_path,
                project_root=project_root,
                python_executable=sys.executable,
                reserve_usd=args.reserve_usd,
                provider=args.provider,
                model=args.model,
                statement_provider=args.statement_provider,
                statement_planner_provider=args.statement_planner_provider,
                statement_planner_model=args.statement_planner_model,
                statement_fallback_provider=args.statement_fallback_provider,
                statement_fallback_model=args.statement_fallback_model,
                statement_judge_provider=args.statement_judge_provider,
                statement_judge_model=args.statement_judge_model,
                statement_candidates=args.statement_candidates,
                statement_candidate_workers=args.statement_candidate_workers,
                model_policy=model_policy,
                environ=execution_env,
                bounded_statements=args.bounded_statements,
                lake_executable=args.lake_executable,
            )
        else:
            results = execute_campaign_wave(
                campaign_path,
                project_root=project_root,
                python_executable=sys.executable,
                worker_count=args.workers,
                reserve_usd=args.reserve_usd,
                wave_budget_usd=args.wave_budget_usd,
                provider=args.provider,
                model=args.model,
                statement_provider=args.statement_provider,
                statement_planner_provider=args.statement_planner_provider,
                statement_planner_model=args.statement_planner_model,
                statement_fallback_provider=args.statement_fallback_provider,
                statement_fallback_model=args.statement_fallback_model,
                statement_judge_provider=args.statement_judge_provider,
                statement_judge_model=args.statement_judge_model,
                statement_candidates=args.statement_candidates,
                statement_candidate_workers=args.statement_candidate_workers,
                model_policy=model_policy,
                environ=execution_env,
                lease_ttl_seconds=args.lease_ttl_seconds,
                bounded_statements=args.bounded_statements,
                lake_executable=args.lake_executable,
            )
            outcome = {
                "executed": bool(results),
                "success": bool(results) and all(item.get("success") for item in results),
                "worker_count": len(results),
                "results": results,
            }
        print(json.dumps(outcome, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if bool(outcome.get("success", not outcome.get("executed"))) else 1
    campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
    if not isinstance(campaign, dict):
        raise CampaignExecutionBlocked("campaign JSON must contain an object")
    summary = describe_next_campaign_action(
        campaign,
        python_executable=sys.executable,
        reserve_usd=args.reserve_usd,
    )
    action = plan_next_campaign_action(campaign, python_executable=sys.executable)
    if action is not None:
        validate_campaign_action_paths(action, project_root=project_root)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
