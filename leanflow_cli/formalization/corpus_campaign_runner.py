"""Plan and execute one resumable action in a book formalization campaign."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import uuid
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from core.project_lean_capacity import MAX_PROJECT_LEAN_CAPACITY
from leanflow_cli.formalization.bounded_statement_refinement import (
    DEFAULT_BOUNDED_STATEMENT_MODEL,
    campaign_statement_source_admission,
    refine_campaign_statement_bounded,
)
from leanflow_cli.formalization.campaign_store import (
    read_campaign,
    update_campaign_file,
)

# These predicates are deliberately reused for explicit operator selectors;
# selection must have exactly the same eligibility and dependency semantics as
# the normal scheduler.
from leanflow_cli.formalization.corpus_campaign import (
    ESCALATION_STATUS,
    MAX_ESCALATION_ATTEMPTS,
    MAX_ESCALATION_SEMANTIC_REPAIRS,
    RETRY_CLASS_INFRASTRUCTURE,
    RETRY_CLASS_SEMANTIC,
    _batch_dependencies_ready,
    _lease_is_active,
    build_campaign,
    classify_campaign_failure,
    classify_campaign_retry_class,
    lease_campaign_batches,
    next_campaign_batch,
    record_campaign_outcome,
    release_campaign_lease,
    statement_escalation_pending,
)
from leanflow_cli.formalization.corpus_planning import source_formalization_complexity
from leanflow_cli.formalization.formalization_document_runner import (
    _approved_blueprint_statement_review_text,
)
from leanflow_cli.formalization.project_reachability import project_target_reachability
from leanflow_cli.formalization.remote_warm_probe import (
    WARM_PROBE_ENV,
    WARMUP_WORKERS_MAX,
)
from leanflow_cli.formalization.statement_review_feedback import (
    REVIEW_FEEDBACK_ENV,
    latest_statement_verdict,
    statement_review_feedback,
)
from leanflow_cli.lean.lean_attempt_location import _multi_attempt_replacement_candidate
from leanflow_cli.lean.lean_module_paths import _lean_imports_from_text
from leanflow_cli.lean.lean_parsing import (
    _declaration_line_index_from_text,
    _text_has_sorry,
)
from leanflow_cli.runtime.toolchain_env import discover_lean_bin
from leanflow_cli.workflows import decomposition_provenance
from leanflow_cli.workflows.project import discover_leanflow_project
from leanflow_cli.workflows.verification_providers import (
    BLUEPRINT_VERIFICATION_TASK,
    run_model_verification_review,
    verification_review_timeout_s,
)
from leanflow_cli.workflows.verification_review import (
    _verification_review_decision,
    _verification_review_findings,
    _verification_review_result_payload,
)


class CampaignExecutionBlocked(RuntimeError):
    """Report campaign state that cannot safely produce an executable action."""


def _normalize_project_path(value: str, project_root: str | Path) -> str:
    """Normalize a path argument relative to ``project_root``.

    Campaigns created from the workspace root may persist paths prefixed with
    the project directory name (for example ``HDP/source/environments.json``).
    When resumed with ``--project-root .../HDP`` that prefix would otherwise be
    joined again, producing ``.../HDP/HDP/...``.  Strip exactly one redundant
    root-name component when the unprefixed candidate is the project-local path.
    Absolute paths and paths for which both candidates are absent are preserved
    for backwards compatibility and security validation still applies.
    """
    raw = str(value or "").strip()
    if not raw:
        return raw
    root = Path(project_root).expanduser().resolve()
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    prefix = root.name + "/"
    prefixed = raw.startswith(prefix)
    dot_prefixed = raw.startswith("./" + prefix)
    if prefixed or dot_prefixed:
        trimmed = raw[len(prefix) :] if prefixed else raw[len("./" + prefix) :]
        raw_exists = (root / raw).exists()
        # Prefer the unprefixed candidate when it exists, or when the prefixed
        # path does not exist (generated targets are often recorded before the
        # file is created).  Preserve an intentionally nested directory when it
        # is already present under the project root.
        if trimmed and not raw_exists:
            return trimmed
    return raw


def _normalize_campaign_action(
    action: CampaignAction, *, project_root: str | Path
) -> CampaignAction:
    """Return an action whose local source/target paths are root-relative."""
    root = Path(project_root).expanduser().resolve()
    argv = list(action.argv)
    try:
        index = argv.index("formalize")
        if index + 1 < len(argv):
            argv[index + 1] = _normalize_project_path(argv[index + 1], root)
    except ValueError:
        pass
    target = _normalize_project_path(action.target_file, root)
    if tuple(argv) == action.argv and target == action.target_file:
        return action
    return replace(action, argv=tuple(argv), target_file=target)


MAX_CAMPAIGN_WORKERS = 4
_ESCALATION_ACTION_RESERVE_FLOOR_USD = 12.0


def _escalation_action_reserve_usd(reserve_usd: float) -> float:
    """Return a larger reservation for an escalation-pending statement batch."""
    return max(float(reserve_usd), _ESCALATION_ACTION_RESERVE_FLOOR_USD)


def _campaign_has_escalation_pending(campaign: Mapping[str, Any]) -> bool:
    """Return whether any statement batch is already in the escalation lane."""
    return any(
        isinstance(batch, Mapping) and statement_escalation_pending(batch)
        for batch in campaign.get("batches", []) or []
    )


def reconcile_campaign_targets(
    campaign: Mapping[str, Any],
    *,
    project_root: str | Path,
    audit_report: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Downgrade stale proof receipts whose generated target is gone or unreachable.

    Campaign JSON is an accounting artifact, not proof authority.  Before a
    resumed scheduler trusts a historical ``proofs_completed`` row, re-check the
    target file, its assigned declarations, and (when a project root module is
    discoverable) reachability from that root.  Never run Lean here; the next
    proof action performs the authoritative check.
    """
    root = Path(project_root).expanduser().resolve()
    updated = {
        **campaign,
        "batches": [dict(item) for item in campaign.get("batches", []) or []],
    }
    stale: list[str] = []
    # Lean projects commonly use an aggregator whose name is not the directory
    # name (the HDP workspace uses ``FateXWork.lean`` at the project root).
    # The old single ``<root-name>.lean`` guess silently disabled reachability
    # checks for such projects and let an unimported proof receipt survive.
    root_candidates = (
        root / "FateXWork.lean",
        root / "Main.lean",
        root / "HDP.lean",
        root / f"{root.name}.lean",
    )
    root_file = next((candidate for candidate in root_candidates if candidate.is_file()), None)

    def module_name(path: Path) -> str:
        try:
            return ".".join(path.relative_to(root).with_suffix("").parts)
        except ValueError:
            return ""

    root_imports: set[str] = set()
    root_imports_available = root_file is not None
    if root_file is not None:
        try:
            root_imports = set(_lean_imports_from_text(root_file.read_text(encoding="utf-8")))
        except OSError:
            root_imports = set()

    # Resolve the complete local import closure, not just the target's parent
    # module.  Aggregators often import a chapter module which imports an item
    # module several levels below it.
    local_imports: dict[str, set[str]] = {}
    if root_imports_available:
        for source_file in root.rglob("*.lean"):
            if any(part in {".git", ".lake", "build"} for part in source_file.parts):
                continue
            try:
                local_imports[module_name(source_file)] = set(
                    _lean_imports_from_text(source_file.read_text(encoding="utf-8"))
                )
            except OSError:
                continue

    reachable_modules: set[str] = set()
    pending_modules = list(root_imports)
    while pending_modules:
        imported = pending_modules.pop()
        if imported in reachable_modules:
            continue
        reachable_modules.add(imported)
        pending_modules.extend(local_imports.get(imported, set()) - reachable_modules)

    scanned = 0
    reason_counts: dict[str, int] = {}
    for batch in updated["batches"]:
        if str(batch.get("status", "") or "") not in {"proofs_completed", "completed"}:
            continue
        scanned += 1
        outcome = dict(batch.get("last_outcome", {}) or {})
        target_file = str(outcome.get("target_file", "") or "").strip()
        invalid_reason = ""
        if not target_file:
            invalid_reason = "proof receipt has no target file"
            target = root
        else:
            target = (root / target_file).resolve()
        if not invalid_reason and (not target.is_relative_to(root) or not target.is_file()):
            invalid_reason = "target file is missing"
        elif not invalid_reason:
            try:
                entries = _declaration_line_index_from_text(target.read_text(encoding="utf-8"))
            except OSError:
                entries = []
            expected = {
                str(value).strip()
                for value in batch.get("declarations", []) or []
                if str(value).strip()
            }
            entry_names = {
                str(entry.get("name", "") or "").strip()
                for entry in entries
                if str(entry.get("name", "") or "").strip()
            }
            relevant = [
                entry for entry in entries if not expected or str(entry.get("name", "")) in expected
            ]
            # A partial declaration index must not validate a multi-declaration
            # receipt.  Every expected declaration has to be present and each
            # expected declaration must be sorry-free; unrelated declarations in
            # the target file do not affect this batch's receipt.
            if (
                not relevant
                or (expected and not expected.issubset(entry_names))
                or any(bool(entry.get("has_sorry")) for entry in relevant)
            ):
                invalid_reason = "target content no longer matches a sorry-free declaration"
            elif root_imports_available:
                target_module = module_name(target)
                reachable = target_module in reachable_modules
                if not reachable:
                    invalid_reason = "target module is not reachable from the project root imports"
        if invalid_reason:
            stale.append(str(batch.get("id", "")))
            reason_counts[invalid_reason] = reason_counts.get(invalid_reason, 0) + 1
            batch["status"] = "proof_retry"
            batch["agent_status"] = "proof_retry"
            batch["last_outcome"] = {
                **outcome,
                "reconciled_stale": True,
                "reconciliation_reason": invalid_reason,
            }
            batch["stale_proof_receipt"] = {
                "reconciled_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "reason": invalid_reason,
                "attempt_count": len(batch.get("attempts", []) or []),
                "target_file": target_file,
            }
    if audit_report is not None:
        audit_report.update(
            {
                "scanned_completed_proofs": scanned,
                "stale_downgraded": len(stale),
                "unchanged_completed_proofs": scanned - len(stale),
                "stale_batch_ids": list(stale),
                "reason_counts": dict(sorted(reason_counts.items())),
                "project_root": str(root),
                "source_files_preserved": True,
            }
        )
    return updated, stale


def reconcile_campaign_targets_report(
    campaign: Mapping[str, Any], *, project_root: str | Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reconcile stale proof receipts and return an operator audit report."""
    report: dict[str, Any] = {}
    updated, _stale = reconcile_campaign_targets(
        campaign, project_root=project_root, audit_report=report
    )
    return updated, report


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
        updated = {
            **current,
            "batches": [dict(item) for item in current.get("batches", []) or []],
        }
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
        in {
            "statement_generation_incomplete",
            "proof_incomplete",
            "verification_timeout",
        }
        and "signal interrupt" not in str(attempt.get("reason", "") or "").lower()
    )
    if policy.escalation_model and failures >= max(1, policy.escalate_after_failures):
        return policy.escalation_model
    stage_model = policy.statement_model if action.stage == "statements" else policy.proof_model
    return stage_model or fallback_model


_STATEMENT_RISK_PATTERNS: tuple[tuple[str, str], ...] = (
    ("source_context_missing", r"source packet is incomplete|source_context"),
    (
        "measurability_integrability",
        r"measurab|integrab|bochner integral|genuine expectation",
    ),
    (
        "extended_value_semantics",
        r"ennreal|ereal|extended[- ](?:real|value)|\binfinity\b",
    ),
    (
        "source_domain_mismatch",
        r"not bidirectionally faithful|changes? the (?:data|domain|object)|source has",
    ),
    (
        "totalized_edge_case",
        r"division by zero|denominator zero|n\s*=\s*0|totalized|truncated natural",
    ),
    (
        "meta_proof_repair",
        r"repair|fixing the proof|auxiliary .* lemma|actual .* theorem",
    ),
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
        "cost_per_completed_batch_usd": (round(spent / completed, 6) if completed else None),
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
    stage: str | None = None,
    batch_id: str | None = None,
) -> CampaignAction | None:
    """Plan proof-first continuation so each approved batch closes before drafting more."""
    requested_stage = str(stage or "").strip()
    requested_batch_id = str(batch_id or "").strip()
    if requested_stage or requested_batch_id:
        if requested_stage not in {"statements", "proofs"}:
            raise CampaignExecutionBlocked("explicit campaign stage must be statements or proofs")
        batches = [item for item in campaign.get("batches", []) or [] if isinstance(item, Mapping)]
        id_counts: dict[str, int] = {}
        for item in batches:
            identifier = str(item.get("id", "") or "").strip()
            if identifier:
                id_counts[identifier] = id_counts.get(identifier, 0) + 1
        duplicate_ids = {identifier for identifier, count in id_counts.items() if count > 1}
        if requested_batch_id and requested_batch_id in duplicate_ids:
            raise CampaignExecutionBlocked(f"campaign batch id is not unique: {requested_batch_id}")
        label_statuses = {
            str(label): str(batch.get("agent_status", batch.get("status", "pending")))
            for batch in batches
            for label in batch.get("labels", []) or []
        }
        eligible = (
            {"pending", "retry", "statement_retry", ESCALATION_STATUS}
            if requested_stage == "statements"
            else {"statements_completed", "proof_retry"}
        )
        selected = next(
            (
                batch
                for batch in batches
                if (not requested_batch_id or str(batch.get("id", "")) == requested_batch_id)
                and str(batch.get("id", "") or "").strip() not in duplicate_ids
                and batch.get("agent_status", batch.get("status")) in eligible
                and not _lease_is_active(batch)
                and _batch_dependencies_ready(
                    batch, stage=requested_stage, label_statuses=label_statuses
                )
            ),
            None,
        )
        if selected is None:
            suffix = f" batch {requested_batch_id}" if requested_batch_id else ""
            raise CampaignExecutionBlocked(f"no eligible {requested_stage} campaign action{suffix}")
        return plan_campaign_batch_action(
            campaign,
            selected,
            stage=requested_stage,
            python_executable=python_executable,
        )
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
    selector: tuple[str, ...]
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
    selector: tuple[str, ...]
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
    stage: str | None = None,
    lease_ttl_seconds: int = 7200,
) -> list[tuple[str, CampaignAction]]:
    """Atomically reserve a proof-first wave while accounting for all reservations."""
    if not 1 <= worker_count <= MAX_CAMPAIGN_WORKERS:
        raise CampaignExecutionBlocked(f"worker count must be between 1 and {MAX_CAMPAIGN_WORKERS}")
    if not math.isfinite(float(reserve_usd)) or reserve_usd <= 0:
        raise CampaignExecutionBlocked("action reservation must be positive")
    requested_stage = str(stage or "").strip()
    if requested_stage and requested_stage not in {"statements", "proofs"}:
        raise CampaignExecutionBlocked("campaign stage must be statements or proofs")

    manifest_path = Path(campaign_path).expanduser().resolve().with_name("book-manifest.json")
    corpus_plan = read_campaign(manifest_path) if manifest_path.is_file() else None

    def claim(current: Mapping[str, Any]):
        if corpus_plan is not None:
            current = build_campaign(corpus_plan, existing=current)
        budget = current.get("budget_usd")
        if budget is None:
            raise CampaignExecutionBlocked("campaign has no explicit budget")
        try:
            budget_value = float(budget)
        except (TypeError, ValueError) as exc:
            raise CampaignExecutionBlocked("campaign budget must be numeric") from exc
        if not math.isfinite(budget_value) or budget_value < 0:
            raise CampaignExecutionBlocked("campaign budget must be finite and non-negative")
        # Leases are durable reservations, not merely coordination markers.
        # Subtract active reservations so concurrent campaign supervisors cannot
        # both admit against the same spent total and overspend the campaign.
        # Legacy leases without this field are charged the current ceiling
        # conservatively rather than being treated as free capacity.
        now = datetime.now(UTC)
        reserved = 0.0
        for batch in current.get("batches", []) or []:
            lease = batch.get("lease") if isinstance(batch, Mapping) else None
            if not isinstance(lease, Mapping):
                continue
            expires = str(lease.get("expires_at", "") or "").strip()
            try:
                expires_at = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            except ValueError:
                expires_at = None
            if expires_at is None or expires_at <= now:
                continue
            try:
                lease_reserve = float(lease.get("reserve_usd", reserve_usd) or reserve_usd)
            except (TypeError, ValueError):
                raise CampaignExecutionBlocked("active lease reservation must be numeric")
            if not math.isfinite(lease_reserve) or lease_reserve < 0:
                raise CampaignExecutionBlocked(
                    "active lease reservation must be finite and non-negative"
                )
            reserved += max(0.0, lease_reserve)
        try:
            spent_value = float(current.get("spent_usd", 0.0) or 0.0)
        except (TypeError, ValueError) as exc:
            raise CampaignExecutionBlocked("campaign spent_usd must be numeric") from exc
        if not math.isfinite(spent_value) or spent_value < 0:
            raise CampaignExecutionBlocked("campaign spent_usd must be finite and non-negative")
        remaining = max(0.0, budget_value - spent_value - reserved)
        capacity = min(worker_count, int(remaining // reserve_usd))
        if capacity <= 0:
            raise CampaignExecutionBlocked(
                "remaining campaign budget does not cover one action reservation"
            )
        working: Mapping[str, Any] = current
        claimed: list[tuple[str, CampaignAction]] = []
        lanes: list[tuple[str, int | None, tuple[str, ...] | None]] = [
            ("proofs", 0, ("routine", "moderate")),
            ("statements", None, ("routine", "moderate")),
            # Preserve the cheap-first ordering, but do not leave paid worker
            # capacity idle merely because one non-complex action exists.
            # Complex work may fill only the slots left by the two lanes above.
            ("proofs", 0, ("complex",)),
            ("statements", None, ("complex",)),
            ("proofs", None, ("complex",)),
            ("proofs", None, ("routine", "moderate")),
        ]
        if requested_stage:
            lanes = [lane for lane in lanes if lane[0] == requested_stage]
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
                reserve_usd=reserve_usd,
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
        selected = _normalize_project_path(action.target_file, root)
    else:
        try:
            formalize_index = action.argv.index("formalize")
            selected = _normalize_project_path(action.argv[formalize_index + 1], root)
        except (ValueError, IndexError) as exc:
            raise CampaignExecutionBlocked("formalization action has no source path") from exc
    path = (root / selected).resolve()
    if not path.is_relative_to(root):
        raise CampaignExecutionBlocked("campaign action path escapes the project")
    if action.stage == "statements" and path.suffix.lower() not in source_extensions:
        raise CampaignExecutionBlocked("formalization source has an unsupported extension")
    if action.stage == "proofs" and path.suffix.lower() != ".lean":
        raise CampaignExecutionBlocked("proof target is not a Lean file")


def _campaign_safe_name(value: str, default: str = "Formalization") -> str:
    """Match the formalization intake's project/module name normalization.

    This intentionally lives here instead of importing ``formalization_documents``:
    that module imports campaign planning, so importing it from the runner would
    introduce an import cycle.  It is only used to derive a *read-only* target
    snapshot path before launching a child workflow.
    """
    words = re.findall(r"[A-Za-z0-9]+", value or "")
    if not words:
        return default
    name = "".join(word[:1].upper() + word[1:] for word in words)
    if not re.match(r"^[A-Za-z_]", name):
        name = f"{default}{name}"
    return name[:80] or default


def _campaign_formalization_target_path(
    action: CampaignAction, *, project_root: str | Path | None
) -> Path | None:
    """Resolve the deterministic target that a formalize action will scaffold.

    Statement actions do not carry ``target_file`` until a successful child
    records its outcome.  Their intake path is nevertheless deterministic from
    the source and QA selector, which lets the parent take a transaction
    snapshot without creating anything itself.  Returning ``None`` is safer
    than guessing when a malformed action cannot be resolved.
    """
    if project_root is None:
        return None
    root = Path(project_root).expanduser().resolve()
    if action.stage == "proofs":
        selected = str(action.target_file or "").strip()
        if not selected:
            return None
        candidate = (root / selected).resolve()
        return candidate if candidate.is_relative_to(root) else None
    if action.stage != "statements":
        return None
    try:
        formalize_index = action.argv.index("formalize")
        source_arg = str(action.argv[formalize_index + 1] or "").strip()
    except (ValueError, IndexError):
        return None
    if not source_arg:
        return None
    source_path = (root / source_arg).resolve()
    if not source_path.is_relative_to(root):
        return None
    # The workflow uses the LeanFlow project manifest name when present, and
    # falls back to the root directory name for bare test projects.
    try:
        project_label = discover_leanflow_project(root).label
    except Exception:
        project_label = root.name
    target = (
        root
        / _campaign_safe_name(project_label or root.name)
        / _campaign_safe_name(source_path.stem, "Document")
        / "Main.lean"
    )
    remainder = tuple(str(item) for item in action.argv[formalize_index + 2 :])
    scope_id = ""
    for index, item in enumerate(remainder):
        if item == "--qa-batch" and index + 1 < len(remainder):
            scope_id = remainder[index + 1].strip()
            break
        if item.startswith("--qa-batch="):
            scope_id = item.partition("=")[2].strip()
            break
    if not scope_id:
        for index, item in enumerate(remainder):
            if item == "--qa-items" and index + 1 < len(remainder):
                labels = remainder[index + 1].strip()
                scope_id = "items-" + labels
                break
            if item.startswith("--qa-items="):
                scope_id = "items-" + item.partition("=")[2].strip()
                break
    if scope_id:
        scope_digest = hashlib.sha256(scope_id.encode("utf-8")).hexdigest()[:8]
        scope_module = _campaign_safe_name(scope_id) + scope_digest.upper()
        target = target.parent / scope_module / "Main.lean"
    return target


def _is_initial_formalization_skeleton(path: Path) -> bool:
    """Return whether ``path`` still contains only the intake import."""
    try:
        return path.read_text(encoding="utf-8").strip() == "import Mathlib"
    except (OSError, UnicodeError):
        return False


def _expected_lean_import_update(before: str, module: str) -> str:
    """Mirror ``_ensure_lean_import``'s deterministic insertion format."""
    lines = before.splitlines()
    insert_at = 0
    while insert_at < len(lines) and not lines[insert_at].strip():
        insert_at += 1
    while insert_at < len(lines) and lines[insert_at].lstrip().startswith("import "):
        insert_at += 1
    lines.insert(insert_at, f"import {module}")
    if insert_at == 0 and len(lines) > 1 and lines[1].strip():
        lines.insert(1, "")
    return "\n".join(lines).rstrip() + "\n"


def _campaign_import_transaction_snapshot(
    action: CampaignAction, *, project_root: str | Path | None, target: Path | None
) -> dict[Path, tuple[str | None, str]]:
    """Snapshot root/parent import-chain files touched by formalize intake."""
    if action.stage != "statements" or target is None:
        return {}
    if project_root is None:
        return {}
    root = Path(project_root).expanduser().resolve()
    try:
        parts = target.relative_to(root).with_suffix("").parts
    except ValueError:
        return {}
    if len(parts) < 2:
        return {}
    root_module = parts[0]
    root_file = root / f"{root_module}.lean"
    candidates: tuple[tuple[Path, str], ...]
    if target.name == "Main.lean" and len(parts) >= 3:
        parent_module = ".".join(parts[:-1])
        parent_file = root / Path(*parts[:-1]).with_suffix(".lean")
        candidates = ((parent_file, ".".join(parts)), (root_file, parent_module))
    else:
        candidates = ((root_file, ".".join(parts)),)
    snapshot: dict[Path, tuple[str | None, str]] = {}
    for path, module in candidates:
        try:
            before = path.read_text(encoding="utf-8") if path.is_file() else None
        except (OSError, UnicodeError):
            continue
        snapshot[path] = (before, module)
    return snapshot


def _cleanup_failed_campaign_imports(
    snapshot: Mapping[Path, tuple[str | None, str]], *, success: bool
) -> None:
    """Undo only the exact import additions made by this failed intake.

    If another process edits a root/parent module while the child runs, the
    post-image no longer equals the deterministic one-import update and is left
    untouched.  Newly-created parent modules containing only that import are
    removed; pre-existing modules are restored byte-for-byte.
    """
    if success:
        return
    for path, (before, module) in snapshot.items():
        try:
            if before is None:
                if path.is_file() and path.read_text(encoding="utf-8") == f"import {module}\n":
                    path.unlink()
            elif path.is_file() and path.read_text(
                encoding="utf-8"
            ) == _expected_lean_import_update(before, module):
                path.write_text(before, encoding="utf-8")
        except (OSError, UnicodeError):
            continue


def _cleanup_failed_campaign_skeleton(
    target: Path | None, *, existed_before: bool, success: bool
) -> bool:
    """Delete only a newly-created, untouched intake skeleton after failure.

    Existing files and any file containing declarations/comments are preserved.
    This narrow check also makes concurrent workers safe: a worker can only
    remove the exact deterministic target it snapshotted as absent.
    """
    if target is None or existed_before or success:
        return False
    if not target.is_file() or not _is_initial_formalization_skeleton(target):
        return False
    try:
        target.unlink()
    except OSError:
        return False
    return True


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
        "(ring; done)",
    ]
    if local_defs:
        definitions = ", ".join(local_defs)
        branches.extend(
            [
                f"(simp_all [{definitions}]; done)",
            ]
        )
    return "by\n  set_option maxHeartbeats 5000 in\n    first | " + " | ".join(branches)


def try_zero_cost_proof_preflight(
    campaign_path: str | Path,
    *,
    project_root: str | Path,
    action: CampaignAction,
    lake_executable: str = "lake",
    worker_id: str = "",
    timeout_s: int = 30,
    failure_diagnostics: list[str] | None = None,
) -> dict[str, Any] | None:
    """Close mechanically trivial approved goals before launching a paid prover.

    A resident LeanProbe REPL is a screening accelerator only.  A successful
    candidate is still checked as an exact project file by ``lake env lean``
    before the source or campaign ledger is changed.
    """
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

    # Most deterministic tactic cascades fail.  Screen them in the resident
    # REPL first so a campaign sweep does not pay one cold Lean process per
    # negative candidate.  Missing/degraded incremental infrastructure falls
    # through to the canonical file check and therefore cannot cause a false
    # acceptance or make this optimization a correctness dependency.
    try:
        from leanflow_cli.lean.lean_incremental import lean_scratch_check

        screen = lean_scratch_check(
            candidate_source,
            cwd=str(root),
            timeout_s=min(max(1, int(timeout_s)), 10),
        )
    except Exception:
        screen = {}
    screen_rejected = bool(
        (screen.get("success") is True and screen.get("ok") is not True)
        or screen.get("timed_out") is True
        or str(screen.get("error_code", "") or "").strip().lower() == "timeout"
    )
    if screen_rejected:
        if failure_diagnostics is not None:
            failure_diagnostics.append(
                str(screen.get("error") or screen.get("output") or "incremental screen rejected")[
                    -4000:
                ]
            )
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
    if worker_id:
        outcome["worker_id"] = worker_id

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
    warmup_workers: int | None = None,
    model_policy: CampaignModelPolicy | None = None,
    environ: Mapping[str, str] | None = None,
    bounded_statements: bool = False,
    lake_executable: str = "lake",
    statement_compile_timeout_seconds: float | int | None = None,
    stage: str | None = None,
    batch_id: str | None = None,
    lease_ttl_seconds: int = 7200,
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
        updated, _stale = reconcile_campaign_targets(updated, project_root=project_root)
        return updated, updated

    campaign = update_campaign_file(path, refresh)
    execution_environ: Mapping[str, str] | None
    explicit_selection = bool(str(stage or "").strip() or str(batch_id or "").strip())
    worker_id = ""
    action: CampaignAction | None = None
    effective_reserve_usd = float(reserve_usd)
    if explicit_selection:
        requested_stage = str(stage or "").strip()
        if requested_stage not in {"statements", "proofs"}:
            raise CampaignExecutionBlocked("explicit campaign stage must be statements or proofs")
        if batch_id is not None and not str(batch_id).strip():
            raise CampaignExecutionBlocked("explicit batch id must not be empty")
        selected_batch_preview = next(
            (
                item
                for item in campaign.get("batches", []) or []
                if isinstance(item, Mapping) and str(item.get("id", "")) == batch_id
            ),
            None,
        )
        escalation_pending = requested_stage == "statements" and (
            (
                isinstance(selected_batch_preview, Mapping)
                and statement_escalation_pending(selected_batch_preview)
            )
            or (batch_id is None and _campaign_has_escalation_pending(campaign))
        )
        if escalation_pending:
            effective_reserve_usd = _escalation_action_reserve_usd(reserve_usd)
        admitted, reason = campaign_execution_admitted(campaign, reserve_usd=effective_reserve_usd)
        if not admitted:
            raise CampaignExecutionBlocked(reason)
        worker_id = f"campaign-{uuid.uuid4().hex}"

        def claim_selected(current: Mapping[str, Any]):
            refreshed, _ = refresh(current)
            claimed, leased = lease_campaign_batches(
                refreshed,
                stage=requested_stage,
                worker_ids=[worker_id],
                ttl_seconds=lease_ttl_seconds,
                reserve_usd=effective_reserve_usd,
                batch_id=str(batch_id or "").strip() or None,
            )
            if not leased:
                selector = f" batch {batch_id}" if batch_id else ""
                raise CampaignExecutionBlocked(
                    f"no eligible {requested_stage} campaign action{selector}"
                )
            return claimed, leased[0]

        # Defer reading/planning the claimed action until the protected
        # execution block below.  A malformed batch or read failure after claim
        # must still persist an infrastructure attempt and release its lease.
        selected_batch = update_campaign_file(path, claim_selected)
        action = CampaignAction(
            stage=requested_stage,
            batch_id=str(selected_batch.get("id", "") or batch_id or ""),
            labels=tuple(str(label) for label in selected_batch.get("labels", []) or []),
            argv=(),
        )
        execution_environ = {
            **dict(environ or os.environ),
            "LEANFLOW_CAMPAIGN_WORKER_ID": worker_id,
        }
    else:
        action = plan_next_campaign_action(campaign, python_executable=python_executable)
        if action is None:
            return {"executed": False, "reason": "campaign has no remaining action"}
        selected_batch_preview = next(
            (
                item
                for item in campaign.get("batches", []) or []
                if isinstance(item, Mapping) and str(item.get("id", "")) == action.batch_id
            ),
            None,
        )
        if (
            action.stage == "statements"
            and isinstance(selected_batch_preview, Mapping)
            and statement_escalation_pending(selected_batch_preview)
        ):
            effective_reserve_usd = _escalation_action_reserve_usd(reserve_usd)
        execution_environ = environ

    try:
        assert action is not None
        if explicit_selection:
            campaign = read_campaign(path)
            action = plan_campaign_batch_action(
                campaign,
                selected_batch,
                stage=requested_stage,
                python_executable=python_executable,
            )
        validate_campaign_action_paths(action, project_root=project_root)
        if not explicit_selection:
            admitted, reason = campaign_execution_admitted(
                campaign, reserve_usd=effective_reserve_usd
            )
            if not admitted:
                raise CampaignExecutionBlocked(reason)
        result = _execute_campaign_action(
            action,
            campaign_path=path,
            campaign=campaign,
            project_root=project_root,
            reserve_usd=effective_reserve_usd,
            provider=provider,
            model=select_campaign_model(
                campaign, action, fallback_model=model, policy=model_policy
            ),
            statement_provider=statement_provider,
            statement_planner_provider=statement_planner_provider,
            statement_planner_model=statement_planner_model,
            statement_fallback_provider=statement_fallback_provider,
            statement_fallback_model=statement_fallback_model,
            statement_judge_provider=statement_judge_provider,
            statement_judge_model=statement_judge_model,
            statement_candidates=statement_candidates,
            statement_candidate_workers=statement_candidate_workers,
            warmup_workers=warmup_workers,
            environ=execution_environ,
            bounded_statements=bounded_statements,
            lake_executable=lake_executable,
            statement_compile_timeout_seconds=statement_compile_timeout_seconds,
        )
        # Native proof summaries omit the nested outcome; expose the lease
        # identity so supervisors can correlate only this child's receipts.
        if worker_id:
            result["worker_id"] = worker_id
        return result
    except BaseException as exc:
        # Preserve an auditable infrastructure attempt before the lease is
        # released.  Re-raise so callers retain cancellation semantics.
        if worker_id:
            _record_campaign_interruption(path, action=action, worker_id=worker_id, error=exc)
        raise
    finally:
        if worker_id:

            def release(current: Mapping[str, Any]):
                try:
                    updated = release_campaign_lease(
                        current, batch_id=action.batch_id, worker_id=worker_id
                    )
                except ValueError:
                    updated = dict(current)
                return updated, None

            update_campaign_file(path, release)


def _recent_campaign_candidate_evidence(
    project_root: str | Path,
    *,
    target_file: str,
    max_records: int = 8,
    max_chars: int = 7000,
) -> str:
    """Recover compact concrete Lean candidates from prior isolated workers."""
    root = Path(project_root).expanduser().resolve()
    target = (root / target_file).resolve()
    activity_root = root / ".leanflow" / "workflow-state" / "workers"
    paths = sorted(
        activity_root.glob("*/activity/agents/*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:24]
    found: list[tuple[str, str]] = []
    for evidence in paths:
        try:
            with evidence.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 2_000_000))
                raw = handle.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        for line in reversed(raw.splitlines()):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            details = dict(record.get("details", {}) or {})
            arguments = dict(details.get("arguments", {}) or {})
            action = str(arguments.get("action", "") or "").replace("-", "_")
            if (
                str(record.get("type", "") or "") != "tool-result"
                or str(details.get("tool", "") or "") != "lean_incremental_check"
                or action not in {"check_target", "check_helper"}
            ):
                continue
            candidate_file = Path(str(arguments.get("file_path", "") or "")).expanduser()
            if not candidate_file.is_absolute():
                candidate_file = root / candidate_file
            if candidate_file.resolve() != target:
                continue
            replacement = str(arguments.get("replacement", "") or "").strip()
            if not replacement:
                continue
            try:
                result = json.loads(str(details.get("result", "") or "{}"))
            except json.JSONDecodeError:
                result = {}
            verdict = "passed" if result.get("ok") is True else "failed"
            diagnostic = str(
                result.get("error", "") or result.get("output", "") or "no diagnostic"
            ).strip()
            block = (
                f"- {action} candidate ({verdict}; {evidence.name}):\n"
                f"```lean\n{replacement[:1400]}\n```\n"
                f"  Lean result: {diagnostic[:500]}"
            )
            declaration_match = re.search(
                r"\b(?:theorem|lemma|def)\s+([A-Za-z0-9_'.]+)", replacement
            )
            fingerprint = (
                f"{action}:{declaration_match.group(1)}"
                if declaration_match
                else hashlib.sha256(replacement.encode("utf-8")).hexdigest()
            )
            if not any(key == fingerprint for key, _ in found):
                found.append((fingerprint, block))
            if len(found) >= max_records:
                rendered = "\n".join(value for _, value in found)
                return rendered[:max_chars]
    return "\n".join(value for _, value in found)[:max_chars]


def _execute_campaign_action_impl(
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
    warmup_workers: int | None = None,
    environ: Mapping[str, str] | None = None,
    bounded_statements: bool = False,
    lake_executable: str = "lake",
    statement_compile_timeout_seconds: float | int | None = None,
) -> dict[str, Any]:
    """Launch one already selected action without re-running global selection."""
    path = Path(campaign_path).expanduser().resolve()
    action = _normalize_campaign_action(action, project_root=project_root)
    validate_campaign_action_paths(action, project_root=project_root)
    worker_id = str((environ or os.environ).get("LEANFLOW_CAMPAIGN_WORKER_ID", "") or "").strip()
    zero_cost_outcome = try_zero_cost_proof_preflight(
        path,
        project_root=project_root,
        action=action,
        lake_executable=lake_executable,
        worker_id=worker_id,
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
    # Feedback belongs to this batch's latest verdict, not the parent process.
    for key in (
        REVIEW_FEEDBACK_ENV,
        "LEANFLOW_FORMALIZATION_REVIEW_FEEDBACK_PROMPT",
        "LEANFLOW_FORMALIZATION_REVIEW_EVIDENCE",
    ):
        child_env.pop(key, None)
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
    child_advisory_timeout_s = max(
        90,
        verification_review_timeout_s(
            child_env.get("LEANFLOW_ADVISORY_VERIFICATION_TIMEOUT_S", "90")
        ),
    )
    child_env["LEANFLOW_ADVISORY_VERIFICATION_TIMEOUT_S"] = str(child_advisory_timeout_s)
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
    # A batch that exhausted the bounded lane gets exactly one unbounded attempt:
    # the bounded lane has a 90s retrieval deadline, three iterations, and cannot
    # look outside its own item, so the items that exhaust it are
    # disproportionately the heavily-cited foundations whose statements need the
    # full agent (book search, Mathlib exploration, multi-declaration output).
    # Falling through to the escalation path below reuses the standard
    # ``workflow formalize`` subprocess, which already has every tool.
    escalating = statement_escalation_pending(selected_batch)
    if escalating and action.stage == "statements":
        child_env["LEANFLOW_FORMALIZATION_ESCALATED"] = "1"
        child_env["LEANFLOW_DISABLE_SOLUTION_RESEARCH"] = "0"
        escalation_receipts = [
            item
            for item in selected_batch.get("attempts", []) or []
            if isinstance(item, Mapping) and bool(item.get("escalated", False))
        ]
        semantic_receipts = sum(
            classify_campaign_retry_class(item) == RETRY_CLASS_SEMANTIC
            for item in escalation_receipts
        )
        infrastructure_receipts = sum(
            classify_campaign_retry_class(item) == RETRY_CLASS_INFRASTRUCTURE
            for item in escalation_receipts
        )
        previous_retry_class = classify_campaign_retry_class(
            dict(selected_batch.get("last_outcome", {}) or {})
        )
        try:
            semantic_repair_limit = max(
                1,
                int(
                    child_env.get(
                        "LEANFLOW_FORMALIZATION_MAX_SEMANTIC_REPAIRS",
                        MAX_ESCALATION_SEMANTIC_REPAIRS,
                    )
                    or MAX_ESCALATION_SEMANTIC_REPAIRS
                ),
            )
        except (TypeError, ValueError):
            semantic_repair_limit = MAX_ESCALATION_SEMANTIC_REPAIRS
        try:
            infrastructure_retry_limit = max(
                1,
                int(
                    child_env.get(
                        "LEANFLOW_FORMALIZATION_MAX_INFRASTRUCTURE_RETRIES",
                        MAX_ESCALATION_ATTEMPTS,
                    )
                    or MAX_ESCALATION_ATTEMPTS
                ),
            )
        except (TypeError, ValueError):
            infrastructure_retry_limit = MAX_ESCALATION_ATTEMPTS
        # Every escalation action is a fresh generator process. The native
        # runner uses this boundary marker to stop after one reviewer BLOCK;
        # the campaign ledger then decides whether another fresh action is
        # admissible. These fields are also included in the outcome receipt for
        # post-hoc case-study reconstruction.
        child_env.update(
            {
                "LEANFLOW_FORMALIZATION_STATEMENT_CONTRACT_GATE": "1",
                "LEANFLOW_FORMALIZATION_ESCALATION_ATTEMPT": str(len(escalation_receipts) + 1),
                "LEANFLOW_FORMALIZATION_ESCALATION_SESSION_ID": uuid.uuid4().hex,
                "LEANFLOW_FORMALIZATION_ESCALATION_SEMANTIC_RECEIPTS": str(semantic_receipts),
                "LEANFLOW_FORMALIZATION_ESCALATION_INFRASTRUCTURE_RECEIPTS": str(
                    infrastructure_receipts
                ),
                "LEANFLOW_FORMALIZATION_FRESH_REVIEW_BOUNDARY": "1",
                "LEANFLOW_FORMALIZATION_MAX_SEMANTIC_REPAIRS": str(semantic_repair_limit),
                "LEANFLOW_FORMALIZATION_MAX_INFRASTRUCTURE_RETRIES": str(
                    infrastructure_retry_limit
                ),
                "LEANFLOW_FORMALIZATION_RETRY_CLASS": previous_retry_class or RETRY_CLASS_SEMANTIC,
            }
        )
        # Source admission precedes the full-tool provider process just as it
        # precedes bounded generation. Candidate lint is enforced again inside
        # that process on every proposed Lean edit and before review/Lean.
        _source, source_issues = campaign_statement_source_admission(
            path,
            project_root=project_root,
            batch_id=action.batch_id,
            timeout_s=int(child_advisory_timeout_s),
        )
        if source_issues:
            diagnostic = "; ".join(source_issues)
            target = _campaign_formalization_target_path(action, project_root=project_root)
            outcome = {
                "stage": "statements",
                "success": False,
                "exit_code": 2,
                "reason": "escalation source admission returned BLOCK: " + diagnostic,
                "target_file": (
                    str(target.relative_to(Path(project_root).resolve())) if target else ""
                ),
                "failure_stage": "source_context",
                "retry_class": RETRY_CLASS_SEMANTIC,
                "review_decision": "BLOCK",
                "review_provider": "deterministic_source_admission",
                "review_findings": list(source_issues),
                "candidate_diagnostics": [
                    {
                        "stage": "source_context",
                        "status": "blocked",
                        "diagnostic": diagnostic,
                    }
                ],
                "final_diagnostic": diagnostic,
                "escalated": True,
                "generator_boundary": "pre_provider_admission",
                "escalation_attempt": len(escalation_receipts) + 1,
                "escalation_session_id": child_env["LEANFLOW_FORMALIZATION_ESCALATION_SESSION_ID"],
                "max_semantic_repairs": semantic_repair_limit,
                "max_infrastructure_retries": infrastructure_retry_limit,
                "cost_usd": 0.0,
                "cost_source": "none",
                "cost_scope": "no_provider_call",
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
                "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            if worker_id:
                outcome["worker_id"] = worker_id
            update_campaign_file(
                path,
                lambda current: (
                    record_campaign_outcome(current, batch_id=action.batch_id, outcome=outcome),
                    None,
                ),
            )
            return {
                "executed": True,
                "stage": action.stage,
                "batch_id": action.batch_id,
                "exit_code": 2,
                "success": False,
                "outcome": outcome,
            }
        # The full agent does not fit the bounded lane's reservation: every
        # escalated attempt in the first HDP escalation wave died on "Per-action
        # USD cost limit reached" at $1.5-1.9 against a $2.0 reserve, spending
        # $33 to close nothing. Give the escalation lane a larger floor so one
        # full multi-call action can finish, still bounded by what the campaign
        # budget actually has left.
        remaining_budget = max(
            0.0,
            float(campaign.get("budget_usd", 0.0) or 0.0)
            - float(campaign.get("spent_usd", 0.0) or 0.0),
        )
        escalation_reserve_usd = min(
            _escalation_action_reserve_usd(reserve_usd),
            remaining_budget,
        )
        child_env["LEANFLOW_ACTION_COST_LIMIT_USD"] = str(escalation_reserve_usd)
        # The review gate closes on three mechanical conditions unrelated to the
        # mathematics, and the escalated agent was never told about them: every
        # BLOCK in the first wave carried the same three findings (blueprint
        # verification line not approved, no project-scope lean_verify, root
        # module missing the import). Spell the contract out so the agent
        # finishes the handoff instead of stopping at a statement the gate
        # then rejects.
        child_env["LEANFLOW_FORMALIZATION_ESCALATION_CONTRACT"] = (
            "You are running one fresh generator/reviewer boundary for one campaign item. "
            "The bounded lane already failed on it. Produce one concrete candidate, run the "
            "required deterministic checks, and allow the independent reviewer to return PASS "
            "or BLOCK. A BLOCK is a semantic retry receipt: do not apply reviewer feedback in "
            "this same process and do not start another autonomous continuation. The campaign "
            "runner will start a fresh process when a semantic retry is still allowed. The "
            "Run the source-fidelity contract lint at review/acceptance handoff (and use a lightweight "
            "guard before any automated Lean check when available; it does not replace the independent reviewer). "
            "Only an independent verifier PASS may stamp approval. In the blueprint inventory entry, include a "
            "`- Source fidelity contract: {...}` JSON object with explicit objects, domains, "
            "hypotheses, conclusion, measure_space, measurability, integrability, and time_domain "
            "where applicable. Tautological predicates and circular target assumptions are BLOCK. The "
            "document-formalization review gate also requires all three of the following before "
            "it will release the item.\n1. Leave the statement verification status pending for the drafting model; "
            "the independent verifier writes the approved wording after PASS. Resolve the source "
            "qualifiers, Lean coverage, and scope-changes bullets to concrete values or `none`.\n"
            "2. Ensure the generated module is reachable from a plain `lake build`: the root "
            "module (or the parent module the root imports) must import this module, and the "
            "target module must not import the root scaffold back.\n3. Run "
            "`lean_verify(mode=project)` as the last verification step, after the files and "
            "imports are in place, and make it pass."
        )
    if bounded_statements and action.stage == "statements" and not escalating:
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
            planner_model=statement_planner_model or model or DEFAULT_BOUNDED_STATEMENT_MODEL,
            judge_provider=statement_judge_provider,
            generator_model=model or DEFAULT_BOUNDED_STATEMENT_MODEL,
            judge_model=statement_judge_model or model or DEFAULT_BOUNDED_STATEMENT_MODEL,
            candidates_per_iteration=statement_candidates,
            candidate_workers=statement_candidate_workers,
            warmup_workers=warmup_workers,
            warm_remote_probe=str(child_env.get(WARM_PROBE_ENV, "")).strip().lower()
            in {"1", "true", "yes", "on"},
            lake_executable=lake_executable,
            max_iterations=3,
            timeout_s=int(child_env.get("LEANFLOW_ADVISORY_VERIFICATION_TIMEOUT_S", "90")),
            compile_timeout_s=statement_compile_timeout_seconds,
            environ=child_env,
        )
        return {
            "executed": True,
            "stage": action.stage,
            "batch_id": action.batch_id,
            "exit_code": int(cast(int, outcome["exit_code"])),
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
        recent_candidates = _recent_campaign_candidate_evidence(
            project_root,
            target_file=action.target_file,
        )
        child_env["LEANFLOW_PROOF_RESUME_EVIDENCE"] = (
            "This is a paid campaign retry. Preserve and use the declarations already present in the "
            f"target file: {declarations or '[none]'}. Previous outcome: "
            f"{str(last_outcome.get('reason', '') or '[unspecified]')}. Do not repeat broad project search, "
            "lean_decompose_helpers, or lean_reasoning_help before executing at least one concrete, "
            "substantive lean_incremental_check that advances the next missing helper or the target."
            + (
                f"\n\nRECENT DURABLE CANDIDATE EVIDENCE:\n{recent_candidates}"
                if recent_candidates
                else ""
            )
        )
    verdict = latest_statement_verdict(selected_batch)
    review_evidence = str(verdict.get("review_evidence", "") or "").strip()
    feedback = statement_review_feedback(verdict) if action.stage == "statements" else ""
    if feedback:
        child_env[REVIEW_FEEDBACK_ENV] = feedback
    if (
        action.stage == "statements"
        and str(verdict.get("review_decision", "") or "").upper() == "BLOCK"
        and review_evidence
    ):
        evidence_path = (Path(project_root).expanduser().resolve() / review_evidence).resolve()
        if (
            evidence_path.is_relative_to(Path(project_root).expanduser().resolve())
            and evidence_path.is_file()
        ):
            child_env["LEANFLOW_FORMALIZATION_REVIEW_EVIDENCE"] = str(evidence_path)
    action_argv = action.argv
    if provider.strip():
        action_argv = (
            *action_argv[:4],
            "--provider",
            provider.strip(),
            *action_argv[4:],
        )
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
        # Prevent a wedged provider/Lean child from occupying the campaign
        # lease forever.  Keep the generous default for normal long proofs,
        # while allowing operators to tune it per campaign.
        child_timeout = float(os.getenv("LEANFLOW_CAMPAIGN_CHILD_TIMEOUT_S", "1800"))
        return_code = process.wait(timeout=max(30.0, child_timeout))
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=10)
        if process.poll() is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
        return_code = 124
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
    if action.stage == "proofs" and return_code != 0:
        try:
            recovered = recover_agent_verified_proof(
                path,
                project_root=project_root,
                batch_id=action.batch_id,
                lake_executable=lake_executable,
            )
        except CampaignExecutionBlocked:
            recovered = None
        if recovered is not None:
            return {
                "executed": True,
                "stage": action.stage,
                "batch_id": action.batch_id,
                "exit_code": 0,
                "success": True,
                "recovered_after_exit_code": int(return_code),
                "outcome": recovered,
            }
    return {
        "executed": True,
        "stage": action.stage,
        "batch_id": action.batch_id,
        "exit_code": int(return_code),
        "success": return_code == 0,
    }


def _execute_campaign_action(
    action: CampaignAction,
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute one action with a narrow failed-intake cleanup transaction.

    ``workflow formalize`` prepares its document context before the provider is
    contacted, so an authentication/connection failure can otherwise leave a
    brand-new ``Main.lean`` containing only ``import Mathlib``.  Snapshot the
    deterministic target before launch and remove it only when the action fails
    and the file is still that untouched skeleton.  The implementation remains
    in a separate function so every existing return/timeout/recovery path gets
    the same cleanup behavior, including exceptions and cancellation.
    """
    project_root = kwargs.get("project_root")
    if project_root is not None:
        action = _normalize_campaign_action(action, project_root=project_root)
    target = _campaign_formalization_target_path(action, project_root=project_root)
    existed_before = bool(target is not None and target.exists())
    import_snapshot = _campaign_import_transaction_snapshot(
        action, project_root=project_root, target=target
    )
    try:
        result = _execute_campaign_action_impl(action, **kwargs)
    except BaseException:
        _cleanup_failed_campaign_skeleton(target, existed_before=existed_before, success=False)
        _cleanup_failed_campaign_imports(import_snapshot, success=False)
        raise
    _cleanup_failed_campaign_skeleton(
        target,
        existed_before=existed_before,
        success=bool(result.get("success", False)),
    )
    _cleanup_failed_campaign_imports(import_snapshot, success=bool(result.get("success", False)))
    return result


def _record_campaign_interruption(
    campaign_path: str | Path,
    *,
    action: CampaignAction,
    worker_id: str,
    error: BaseException,
) -> bool:
    """Persist an infrastructure failure before a claimed lease is released.

    A worker can be cancelled after claiming a batch but before the child
    process records its normal outcome.  Recording the failure while the lease
    owner is still present makes that interval auditable and prevents a
    cancelled action from silently returning to ``pending`` with zero attempts.
    The lease-owner guard also makes this safe when a late exception races a
    normal outcome commit or lease reclamation.
    """
    if not worker_id:
        return False
    reason = f"{type(error).__name__}: {error}"[:2000]
    timed_out = isinstance(error, subprocess.TimeoutExpired) or "timeout" in reason.lower()
    cancelled = isinstance(error, (KeyboardInterrupt, SystemExit)) or "interrupt" in reason.lower()
    outcome = {
        "stage": action.stage,
        "batch_id": action.batch_id,
        "worker_id": worker_id,
        "success": False,
        "exit_code": 124 if timed_out else 130 if cancelled else 1,
        "reason": reason,
        "failure_class": "infrastructure",
        "infrastructure_failure": True,
        "cancelled": cancelled,
        "timed_out": timed_out,
        "aborted": cancelled,
        "inflight": True,
        "interruption_kind": ("aborted" if cancelled else "timeout" if timed_out else "error"),
        "recovery_receipt": {
            "kind": "campaign_interruption",
            "worker_id": worker_id,
            "stage": action.stage,
            "batch_id": action.batch_id,
            "recorded_before_lease_release": True,
        },
        "cost_usd": 0.0,
        "cost_source": "campaign_worker_interrupted",
        "cost_scope": "no_additional_provider_call",
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }

    def commit(current: Mapping[str, Any]):
        batch = next(
            (
                item
                for item in current.get("batches", []) or []
                if isinstance(item, Mapping) and str(item.get("id", "")) == action.batch_id
            ),
            None,
        )
        if not isinstance(batch, Mapping):
            return current, False
        lease = batch.get("lease")
        if not isinstance(lease, Mapping) or str(lease.get("worker_id", "")) != worker_id:
            # No active lease means another path already committed/reclaimed the
            # action; never append a duplicate late failure.
            return current, False
        attempts = batch.get("attempts", []) or []
        if any(
            isinstance(item, Mapping)
            and str(item.get("worker_id", "")) == worker_id
            and str(item.get("stage", "")) == action.stage
            and bool(item.get("infrastructure_failure", False))
            for item in attempts
        ):
            return current, False
        return (
            record_campaign_outcome(current, batch_id=action.batch_id, outcome=outcome),
            True,
        )

    return bool(update_campaign_file(campaign_path, commit))


def execute_campaign_wave(
    campaign_path: str | Path,
    *,
    project_root: str | Path,
    python_executable: str,
    worker_count: int,
    reserve_usd: float,
    wave_budget_usd: float | None = None,
    stage: str | None = None,
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
    warmup_workers: int | None = None,
    model_policy: CampaignModelPolicy | None = None,
    environ: Mapping[str, str] | None = None,
    lease_ttl_seconds: int = 7200,
    bounded_statements: bool = False,
    lake_executable: str = "lake",
    statement_compile_timeout_seconds: float | int | None = None,
) -> list[dict[str, Any]]:
    """Run a budget-safe wave of distinct leased batches concurrently."""
    path = Path(campaign_path).expanduser().resolve()
    if not 1 <= worker_count <= MAX_CAMPAIGN_WORKERS:
        raise CampaignExecutionBlocked(f"worker count must be between 1 and {MAX_CAMPAIGN_WORKERS}")
    requested_stage = str(stage or "").strip()
    if requested_stage and requested_stage not in {"statements", "proofs"}:
        raise CampaignExecutionBlocked("campaign stage must be statements or proofs")
    if wave_budget_usd is not None:
        if wave_budget_usd <= 0:
            raise CampaignExecutionBlocked("wave budget must be positive")
        # ``reserve_usd`` is historically a per-action ceiling.  A separate
        # wave ceiling makes concurrency safe without silently multiplying the
        # operator's intended total spend by the worker count.
        action_reserve_usd = min(float(reserve_usd), float(wave_budget_usd) / worker_count)
    else:
        action_reserve_usd = float(reserve_usd)
    if requested_stage != "proofs" and _campaign_has_escalation_pending(read_campaign(path)):
        action_reserve_usd = _escalation_action_reserve_usd(action_reserve_usd)
    if wave_budget_usd is not None and action_reserve_usd * worker_count > wave_budget_usd:
        raise CampaignExecutionBlocked(
            "wave budget does not cover the requested workers' escalation reservations"
        )
    claims = lease_next_campaign_actions(
        path,
        worker_count=worker_count,
        python_executable=python_executable,
        reserve_usd=action_reserve_usd,
        stage=requested_stage or None,
        lease_ttl_seconds=lease_ttl_seconds,
    )
    if not claims:
        return []

    def run_claim(worker_id: str, action: CampaignAction) -> dict[str, Any]:
        try:
            snapshot = read_campaign(path)
            selected_batch = next(
                batch for batch in snapshot["batches"] if batch["id"] == action.batch_id
            )
            if (
                wave_budget_usd is not None
                and action.stage == "statements"
                and statement_escalation_pending(selected_batch)
                and _escalation_action_reserve_usd(action_reserve_usd) > action_reserve_usd
            ):
                raise CampaignExecutionBlocked(
                    "wave budget cannot raise a leased action's escalation reservation"
                )
            selected_model = select_campaign_model(
                snapshot, action, fallback_model=model, policy=model_policy
            )
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
                warmup_workers=warmup_workers,
                environ={
                    **dict(environ or os.environ),
                    "LEANFLOW_CAMPAIGN_WORKER_ID": worker_id,
                },
                bounded_statements=bounded_statements,
                lake_executable=lake_executable,
                statement_compile_timeout_seconds=statement_compile_timeout_seconds,
            )
            result["model"] = selected_model
            return result
        except BaseException as exc:
            persisted = False
            with contextlib.suppress(Exception):
                persisted = _record_campaign_interruption(
                    path, action=action, worker_id=worker_id, error=exc
                )
            return {
                "executed": True,
                "stage": action.stage,
                "batch_id": action.batch_id,
                "worker_id": worker_id,
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
                "infrastructure_failure_persisted": persisted,
            }
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
    pool = ThreadPoolExecutor(max_workers=len(claims), thread_name_prefix="leanflow-campaign")
    futures = {
        pool.submit(run_claim, worker_id, action): (worker_id, action)
        for worker_id, action in claims
    }
    try:
        for future in as_completed(futures):
            worker_id, action = futures[future]
            try:
                result = future.result()
            except BaseException as exc:
                persisted = False
                with contextlib.suppress(Exception):
                    persisted = _record_campaign_interruption(
                        path, action=action, worker_id=worker_id, error=exc
                    )
                result = {
                    "executed": True,
                    "stage": action.stage,
                    "batch_id": action.batch_id,
                    "worker_id": worker_id,
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "infrastructure_failure_persisted": persisted,
                }
            else:
                result["worker_id"] = worker_id
            results.append(result)
    except BaseException as exc:
        # Ctrl-C can interrupt ``as_completed`` while children are still
        # running. Persist failures and release every outstanding lease before
        # returning control to the caller, instead of leaving ghost leases
        # until their TTL expires.
        for future in futures:
            future.cancel()
        for worker_id, action in claims:
            with contextlib.suppress(Exception):
                _record_campaign_interruption(path, action=action, worker_id=worker_id, error=exc)
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        pool.shutdown(wait=True)
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
    if stage == "proofs":
        try:
            verified_source = (Path(project_root).expanduser().resolve() / target_file).read_text(
                encoding="utf-8"
            )
        except OSError as exc:
            raise CampaignExecutionBlocked("verified proof target could not be read") from exc
        if _text_has_sorry(verified_source):
            raise CampaignExecutionBlocked(
                "local proof verification target still contains sorry/admit/sorryAx"
            )
    proof_obligations = (completed.stdout + completed.stderr).count("declaration uses `sorry`")
    if stage == "proofs" and proof_obligations:
        raise CampaignExecutionBlocked(
            f"local proof verification still reports {proof_obligations} sorry declaration(s)"
        )
    integration = project_target_reachability(project_root, target_file)
    outcome = {
        "stage": stage,
        "success": True,
        "exit_code": 0,
        "reason": f"locally verified {stage} repair",
        "target_file": target_file,
        "root_reachable": integration["root_reachable"],
        "integration_status": integration["integration_status"],
        "integration_root_file": integration["root_file"],
        "proof_obligations": proof_obligations,
        "cost_usd": 0.0,
        "cost_source": "local",
        "cost_scope": "no_provider_call",
        "provenance": "manual_gold",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    # Re-read under the campaign transaction: another worker may have completed
    # or reclaimed this batch while Lean was running.
    update_campaign_file(
        path,
        lambda current: (
            record_campaign_outcome(current, batch_id=batch_id, outcome=outcome),
            None,
        ),
    )
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
        stage="proofs",
        batch_id=batch_id,
        labels=(),
        argv=(),
        target_file=selected_target,
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
    integration = project_target_reachability(root, selected_target)
    outcome = {
        "stage": "statements",
        "success": True,
        "exit_code": 0,
        "reason": "recovered independently reviewed agent statement handoff",
        "target_file": selected_target,
        "root_reachable": integration["root_reachable"],
        "integration_status": integration["integration_status"],
        "integration_root_file": integration["root_file"],
        "proof_obligations": proof_obligations,
        "cost_usd": 0.0,
        "cost_source": "review_reuse",
        "cost_scope": "no_provider_call",
        "provenance": "agent",
        "review_evidence": str(evidence.relative_to(root)),
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    update_campaign_file(
        path,
        lambda current: (
            record_campaign_outcome(current, batch_id=batch_id, outcome=outcome),
            None,
        ),
    )
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
        stage="proofs",
        batch_id=batch_id,
        labels=(),
        argv=(),
        target_file=selected_target,
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
        [
            lake_executable,
            "build",
            *([project_build_target] if project_build_target else []),
        ],
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
            timeout_s=verification_review_timeout_s(timeout_s),
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
    integration = project_target_reachability(root, selected_target)
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
        "root_reachable": integration["root_reachable"],
        "integration_status": integration["integration_status"],
        "integration_root_file": integration["root_file"],
        "proof_obligations": target_text.count("sorry"),
        "cost_usd": (
            float(payload.get("cost_usd", 0.0) or 0.0)
            if payload.get("pricing_known", False)
            else 0.0
        ),
        "pricing_known": bool(payload.get("pricing_known", False)),
        "cost_source": (
            ("reviewer_token_usage" if payload.get("total_tokens") else "unavailable")
            if payload.get("pricing_known", False)
            else "cost_unavailable"
        ),
        "cost_scope": "independent_statement_reviewer",
        "provenance": "agent",
        "review_evidence": str(evidence.relative_to(root)),
        "review_decision": decision,
        "review_provider": str(payload.get("provider", "") or provider),
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

    if str(payload.get("status", "") or "") != "ok":
        outcome["infrastructure_failure"] = True
        outcome["retry_class"] = RETRY_CLASS_INFRASTRUCTURE
        outcome["review_decision"] = ""
        outcome["review_findings"] = []

    def commit(current: Mapping[str, Any]):
        return (
            record_campaign_outcome(current, batch_id=batch_id, outcome=outcome),
            None,
        )

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
    indexed_declarations = _declaration_line_index_from_text(source)
    target_declaration = next(
        (
            (str(item.get("name", "") or ""), str(item.get("text", "") or ""))
            for item in indexed_declarations
            if str(item.get("name", "") or "")
            in {str(value) for value in batch.get("declarations", []) or []}
            or (
                _text_has_sorry(str(item.get("text", "") or ""))
                and str(item.get("kind", "") or "") in {"theorem", "lemma"}
            )
        ),
        ("", ""),
    )
    # A crash/finalization race may occur after the successful check_target
    # candidate was already CAS-committed.  Older campaign batches do not
    # carry a declaration list, so recover the only theorem/lemma in that
    # file and authenticate it against the durable tool-result evidence below.
    if not target_declaration[0]:
        proof_declarations = [
            item
            for item in indexed_declarations
            if str(item.get("kind", "") or "") in {"theorem", "lemma"}
        ]
        if len(proof_declarations) == 1:
            target_declaration = (
                str(proof_declarations[0].get("name", "") or ""),
                str(proof_declarations[0].get("text", "") or ""),
            )
    declaration_name, old_declaration = target_declaration
    activity_matches: list[tuple[str, str, Path]] = []
    activity_roots = [root / ".leanflow" / "workflow-state" / "activity" / "agents"]
    activity_roots.extend(
        (root / ".leanflow" / "workflow-state" / "workers").glob("*/activity/agents")
    )
    allowed_axioms = {"propext", "Classical.choice", "Quot.sound"}
    for activity_root in activity_roots:
        if not activity_root.is_dir():
            continue
        for evidence_path in activity_root.glob("*.jsonl"):
            for line in evidence_path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                details = dict(record.get("details", {}) or {})
                arguments = dict(details.get("arguments", {}) or {})
                if (
                    str(record.get("type", "") or "") != "tool-result"
                    or str(details.get("tool", "") or "") != "lean_incremental_check"
                    or str(arguments.get("action", "") or "").replace("-", "_") != "check_target"
                    or str(arguments.get("theorem_id", "") or "") != declaration_name
                ):
                    continue
                candidate_file = Path(str(arguments.get("file_path", "") or "")).expanduser()
                if not candidate_file.is_absolute():
                    candidate_file = root / candidate_file
                if candidate_file.resolve() != target:
                    continue
                try:
                    checked = json.loads(str(details.get("result", "") or "{}"))
                except json.JSONDecodeError:
                    continue
                axioms = {
                    str(value or "").strip()
                    for value in checked.get("axiom_profile_axioms", []) or []
                }
                replacement_text = str(arguments.get("replacement", "") or "").strip()
                if not (
                    checked.get("ok") is True
                    and checked.get("valid_without_sorry") is True
                    and checked.get("has_errors") is False
                    and checked.get("has_sorry") is False
                    and checked.get("replacement_matches_target") is True
                    and checked.get("axiom_profile_checked") is True
                    and not list(checked.get("axiom_profile_blockers") or [])
                    and not (axioms - allowed_axioms)
                    and replacement_text
                ):
                    continue
                entries = [
                    item
                    for item in _declaration_line_index_from_text(replacement_text)
                    if str(item.get("name", "") or "") == declaration_name
                ]
                if len(entries) != 1:
                    continue
                recovered = str(entries[0].get("text", "") or "").strip()
                if (
                    not recovered
                    or _text_has_sorry(recovered)
                    or re.sub(r"\s+", " ", recovered.partition(":=")[0]).strip()
                    != re.sub(r"\s+", " ", old_declaration.partition(":=")[0]).strip()
                ):
                    continue
                activity_matches.append(
                    (str(record.get("timestamp", "") or ""), recovered, evidence_path)
                )
    tactic = ""
    if matches:
        _timestamp, payload, evidence = max(matches, key=lambda item: item[0])
        try:
            target_line = int(payload.get("line", 0) or 0)
            raw_column = payload.get("column")
            column = int(raw_column) if raw_column not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise CampaignExecutionBlocked(
                "verified candidate has invalid source coordinates"
            ) from exc
        tactic = str(payload["verified_attempts"][0]).strip()
        replacement = _multi_attempt_replacement_candidate(target, target_line, column, tactic)
        if replacement is None:
            raise CampaignExecutionBlocked("verified candidate no longer matches current source")
        declaration_name, declaration = replacement
    elif activity_matches:
        _timestamp, declaration, evidence = max(activity_matches, key=lambda item: item[0])
        tactic = "exact target replacement recovered from successful LeanProbe check_target"
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
        if separator < 0 or _text_has_sorry(helper_declaration[separator:]):
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
    update_campaign_file(
        path,
        lambda current: (
            record_campaign_outcome(current, batch_id=batch_id, outcome=outcome),
            None,
        ),
    )
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
    parser.add_argument(
        "--warmup-workers",
        type=int,
        default=None,
        help=f"remote warm LeanProbe session capacity (1-{WARMUP_WORKERS_MAX})",
    )
    parser.add_argument(
        "--warm-remote-probe",
        action="store_true",
        help="opt in to the bounded remote LeanProbe warm service for statement screening",
    )
    parser.add_argument("--proof-model", default="")
    parser.add_argument("--escalation-model", default="")
    parser.add_argument("--escalate-after-failures", type=int, default=2)
    parser.add_argument("--reasoning-effort", default="")
    parser.add_argument("--batch-item-limit", type=int, default=None)
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--stage",
        choices=("statements", "proofs"),
        default=None,
        help="explicitly constrain execution to one campaign stage (default keeps proof-first planning)",
    )
    parser.add_argument(
        "--batch-id",
        default=None,
        help="explicitly execute this eligible batch; must be paired with --stage",
    )
    parser.add_argument(
        "--reconcile-only",
        action="store_true",
        help="reconcile stale proof receipts, persist an audit report, and do no paid work",
    )
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
    parser.add_argument(
        "--statement-compile-timeout-seconds",
        type=float,
        default=None,
        help="per-candidate remote Lean timeout (capped by the bounded lane)",
    )
    parser.add_argument("--project-build-target", default="")
    parser.add_argument("--local-target", default="")
    # HDP restricted campaigns keep orchestration local but run every Lake/Lean
    # process through the checked-in remote wrapper. Callers may still provide
    # an explicit executable for isolated unit tests or another approved host.
    parser.add_argument(
        "--lake-executable",
        default=str(Path(__file__).resolve().parents[2] / "remote-bin" / "lake"),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    campaign_path = Path(args.campaign).expanduser().resolve()
    project_root = Path(args.project_root).expanduser().resolve()
    if args.reconcile_only and args.execute:
        parser.error("--reconcile-only cannot be combined with --execute")
    if args.batch_id and not args.stage:
        parser.error("--batch-id requires --stage")
    if args.warmup_workers is not None and not 1 <= args.warmup_workers <= WARMUP_WORKERS_MAX:
        parser.error(f"--warmup-workers must be between 1 and {WARMUP_WORKERS_MAX}")
    lake_path = Path(args.lake_executable).expanduser()
    if not lake_path.is_absolute():
        cwd_lake = lake_path.resolve()
        project_lake = (project_root / lake_path).resolve()
        if cwd_lake.is_file():
            args.lake_executable = str(cwd_lake)
        elif project_lake.is_file():
            args.lake_executable = str(project_lake)
        else:
            # Native workflow subprocesses may receive a sanitized PATH, and an
            # elan-managed toolchain under <root>/.elan-home is not on PATH at
            # all.  Resolve lake to an absolute path here, because the bounded
            # statement lane invokes it directly instead of through the managed
            # workflow env, and a bare "lake" would raise FileNotFoundError
            # after the paid generator call had already been made.
            resolved_lake = shutil.which(str(lake_path))
            if resolved_lake:
                args.lake_executable = resolved_lake
            else:
                lean_bin = discover_lean_bin(project_root)
                if lean_bin is not None and os.access(lean_bin / lake_path.name, os.X_OK):
                    args.lake_executable = str(lean_bin / lake_path.name)
    if args.reconcile_only:
        report: dict[str, Any] = {
            "mode": "reconciliation-only",
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }

        def reconcile_only(current: Mapping[str, Any]):
            updated, details = reconcile_campaign_targets_report(current, project_root=project_root)
            report.update(details)
            updated["last_reconciliation_audit"] = dict(report)
            return updated, report

        persisted_report = update_campaign_file(campaign_path, reconcile_only)
        print(json.dumps(persisted_report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    refresh_campaign_source_complexity(campaign_path, project_root=project_root)
    if args.refine_statement_bounded:
        if args.reserve_usd is None or args.reserve_usd <= 0:
            parser.error("--refine-statement-bounded requires a positive --reserve-usd")
        if not 1 <= args.max_statement_iterations <= 3:
            parser.error("--max-statement-iterations must be between 1 and 3")
        if not 1 <= args.statement_candidates <= 8:
            parser.error("--statement-candidates must be between 1 and 8")
        bounded_model = (
            args.model
            or args.statement_model
            or args.statement_planner_model
            or args.statement_judge_model
            or DEFAULT_BOUNDED_STATEMENT_MODEL
        )
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
            planner_model=args.statement_planner_model or bounded_model,
            judge_provider=args.statement_judge_provider or args.review_provider,
            generator_model=args.statement_model or args.model or bounded_model,
            judge_model=args.statement_judge_model or args.review_model or bounded_model,
            lake_executable=args.lake_executable,
            max_iterations=args.max_statement_iterations,
            candidates_per_iteration=args.statement_candidates,
            candidate_workers=args.statement_candidate_workers,
            warmup_workers=args.warmup_workers,
            warm_remote_probe=args.warm_remote_probe,
            timeout_s=args.review_timeout_seconds,
            compile_timeout_s=args.statement_compile_timeout_seconds,
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
        if args.batch_item_limit is not None and args.batch_item_limit <= 0:
            parser.error("--batch-item-limit must be positive")
        manifest_path = campaign_path.with_name("book-manifest.json")
        corpus_plan: dict[str, Any] | None = None
        if args.batch_item_limit is not None:
            if not manifest_path.is_file():
                raise CampaignExecutionBlocked("book-manifest.json is required to repartition")
            raw_plan = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(raw_plan, dict):
                raise CampaignExecutionBlocked("book manifest JSON must contain an object")
            corpus_plan = raw_plan

        def update_options(
            current: Mapping[str, Any],
        ) -> tuple[Mapping[str, Any], None]:
            updated = dict(current)
            if corpus_plan is not None:
                updated = build_campaign(
                    corpus_plan,
                    existing={**updated, "batch_item_limit": args.batch_item_limit},
                )
            if args.budget_usd is not None:
                if args.budget_usd < float(updated.get("spent_usd", 0.0) or 0.0):
                    parser.error("--budget-usd cannot be below already-spent campaign cost")
                updated["budget_usd"] = args.budget_usd
            return updated, None

        update_campaign_file(campaign_path, update_options)
    if args.execute:
        if args.reserve_usd is None:
            parser.error("--execute requires --reserve-usd")
        if not 1 <= args.workers <= MAX_CAMPAIGN_WORKERS:
            parser.error(f"--workers must be between 1 and {MAX_CAMPAIGN_WORKERS}")
        if args.batch_id and args.workers != 1:
            parser.error("--batch-id selector requires --workers=1")
        if args.wave_budget_usd is not None and args.wave_budget_usd <= 0:
            parser.error("--wave-budget-usd must be positive")
        if not 1 <= args.lean_slots <= MAX_PROJECT_LEAN_CAPACITY:
            parser.error(f"--lean-slots must be between 1 and {MAX_PROJECT_LEAN_CAPACITY}")
        if not 1 <= args.statement_candidates <= 8:
            parser.error("--statement-candidates must be between 1 and 8")
        if args.statement_candidate_workers <= 0:
            parser.error("--statement-candidate-workers must be positive")
        execution_env = {
            **os.environ,
            "LEANFLOW_PROJECT_LEAN_CAPACITY": str(args.lean_slots),
        }
        if args.warm_remote_probe:
            execution_env[WARM_PROBE_ENV] = "1"
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
                warmup_workers=args.warmup_workers,
                model_policy=model_policy,
                environ=execution_env,
                bounded_statements=args.bounded_statements,
                lake_executable=args.lake_executable,
                statement_compile_timeout_seconds=args.statement_compile_timeout_seconds,
                stage=args.stage,
                batch_id=args.batch_id,
                lease_ttl_seconds=args.lease_ttl_seconds,
            )
        else:
            results = execute_campaign_wave(
                campaign_path,
                project_root=project_root,
                python_executable=sys.executable,
                worker_count=args.workers,
                reserve_usd=args.reserve_usd,
                wave_budget_usd=args.wave_budget_usd,
                stage=args.stage,
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
                warmup_workers=args.warmup_workers,
                model_policy=model_policy,
                environ=execution_env,
                lease_ttl_seconds=args.lease_ttl_seconds,
                bounded_statements=args.bounded_statements,
                lake_executable=args.lake_executable,
                statement_compile_timeout_seconds=args.statement_compile_timeout_seconds,
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
