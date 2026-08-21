"""Plan and execute one resumable action in a book formalization campaign."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.utils import atomic_json_write
from leanflow_cli.formalization.corpus_campaign import (
    build_campaign,
    next_campaign_batch,
    record_campaign_outcome,
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
    selected = action.target_file if action.stage == "proofs" else action.argv[-3]
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
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Execute exactly one admitted action; the native runner commits its outcome."""
    path = Path(campaign_path).expanduser().resolve()
    campaign = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(campaign, dict):
        raise CampaignExecutionBlocked("campaign JSON must contain an object")
    action = plan_next_campaign_action(campaign, python_executable=python_executable)
    if action is None:
        return {"executed": False, "reason": "campaign has no remaining action"}
    validate_campaign_action_paths(action, project_root=project_root)
    admitted, reason = campaign_execution_admitted(campaign, reserve_usd=reserve_usd)
    if not admitted:
        raise CampaignExecutionBlocked(reason)
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
    action_argv = action.argv
    if provider.strip():
        action_argv = (*action.argv[:4], "--provider", provider.strip(), *action.argv[4:])
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
    parser.add_argument("--batch-item-limit", type=int, default=None)
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--accept-local-statement", default="")
    parser.add_argument("--accept-agent-reviewed-statement", default="")
    parser.add_argument("--review-file", default="")
    parser.add_argument("--accept-local-proof", default="")
    parser.add_argument("--local-target", default="")
    parser.add_argument("--lake-executable", default="lake")
    args = parser.parse_args(list(argv) if argv is not None else None)
    campaign_path = Path(args.campaign).expanduser().resolve()
    project_root = Path(args.project_root).expanduser().resolve()
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
        outcome = execute_next_campaign_action(
            campaign_path,
            project_root=project_root,
            python_executable=sys.executable,
            reserve_usd=args.reserve_usd,
            provider=args.provider,
        )
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
