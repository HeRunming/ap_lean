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
from leanflow_cli.formalization.campaign_store import read_campaign, update_campaign_file
from leanflow_cli.formalization.corpus_campaign import (
    build_campaign,
    classify_campaign_failure,
    lease_campaign_batches,
    next_campaign_batch,
    record_campaign_outcome,
    release_campaign_lease,
)
from leanflow_cli.formalization.formalization_document_runner import (
    _approved_blueprint_statement_review_text,
)
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

    statement_batch = next_campaign_batch(campaign, stage="statements")
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
    isolated = {**campaign, "batches": [{**batch, "lease": None}]}
    action = plan_next_campaign_action(isolated, python_executable=python_executable)
    if action is None or action.stage != stage:
        raise CampaignExecutionBlocked(f"batch {batch.get('id', '')} is not eligible for {stage}")
    return action


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
        for stage in ("proofs", "statements"):
            open_slots = capacity - len(claimed)
            if open_slots <= 0:
                break
            worker_ids = [f"campaign-{uuid.uuid4().hex}" for _ in range(open_slots)]
            working, leased = lease_campaign_batches(
                working,
                stage=stage,
                worker_ids=worker_ids,
                ttl_seconds=lease_ttl_seconds,
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
    model_policy: CampaignModelPolicy | None = None,
    environ: Mapping[str, str] | None = None,
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
        environ=environ,
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
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Launch one already selected action without re-running global selection."""
    path = Path(campaign_path).expanduser().resolve()
    validate_campaign_action_paths(action, project_root=project_root)
    child_env = dict(environ or os.environ)
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
    last_outcome = dict(selected_batch.get("last_outcome", {}) or {})
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
    provider: str = "",
    model: str = "",
    model_policy: CampaignModelPolicy | None = None,
    environ: Mapping[str, str] | None = None,
    lease_ttl_seconds: int = 7200,
) -> list[dict[str, Any]]:
    """Run a budget-safe wave of distinct leased batches concurrently."""
    path = Path(campaign_path).expanduser().resolve()
    claims = lease_next_campaign_actions(
        path,
        worker_count=worker_count,
        python_executable=python_executable,
        reserve_usd=reserve_usd,
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
                reserve_usd=reserve_usd,
                provider=provider,
                model=selected_model,
                environ={**dict(environ or os.environ), "LEANFLOW_CAMPAIGN_WORKER_ID": worker_id},
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
        "representation assumptions are acceptable only when disclosed. The `sorry` bodies are "
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


def main(argv: Sequence[str] | None = None) -> int:
    """Inspect a campaign or explicitly execute one budget-admitted action."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--reserve-usd", type=float, default=None)
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--statement-model", default="")
    parser.add_argument("--proof-model", default="")
    parser.add_argument("--escalation-model", default="")
    parser.add_argument("--escalate-after-failures", type=int, default=2)
    parser.add_argument("--reasoning-effort", default="")
    parser.add_argument("--batch-item-limit", type=int, default=None)
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--lean-slots", type=int, default=1)
    parser.add_argument("--lease-ttl-seconds", type=int, default=7200)
    parser.add_argument("--accept-local-statement", default="")
    parser.add_argument("--accept-agent-reviewed-statement", default="")
    parser.add_argument("--review-file", default="")
    parser.add_argument("--accept-local-proof", default="")
    parser.add_argument("--review-agent-statement", default="")
    parser.add_argument("--review-provider", default="main")
    parser.add_argument("--review-model", default="")
    parser.add_argument("--review-timeout-seconds", type=int, default=90)
    parser.add_argument("--project-build-target", default="")
    parser.add_argument("--local-target", default="")
    parser.add_argument("--lake-executable", default="lake")
    args = parser.parse_args(list(argv) if argv is not None else None)
    campaign_path = Path(args.campaign).expanduser().resolve()
    project_root = Path(args.project_root).expanduser().resolve()
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
        if not 1 <= args.lean_slots <= 8:
            parser.error("--lean-slots must be between 1 and 8")
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
                model_policy=model_policy,
                environ=execution_env,
            )
        else:
            results = execute_campaign_wave(
                campaign_path,
                project_root=project_root,
                python_executable=sys.executable,
                worker_count=args.workers,
                reserve_usd=args.reserve_usd,
                provider=args.provider,
                model=args.model,
                model_policy=model_policy,
                environ=execution_env,
                lease_ttl_seconds=args.lease_ttl_seconds,
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
