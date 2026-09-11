"""Gate current full-tool formalization artifacts with bounded statement lint."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from leanflow_cli.formalization.bounded_statement_refinement import (
    campaign_statement_source,
    lint_generated_statement_contract,
)
from leanflow_cli.formalization.formalization_document_runner import (
    _blueprint_bullet_value,
    _blueprint_source_inventory_entries,
    _document_formalization_manifest_blocks,
)
from leanflow_cli.formalization.formalization_generated_lean import (
    _formalization_generated_lean_text,
)
from leanflow_cli.lean.lean_parsing import (
    _declaration_line_index_from_text,
    _strip_lean_comments_and_strings,
)
from leanflow_cli.native.native_config import _project_root, _read_text_env, _workflow_kind


def document_statement_contract_gate(
    active_file: str,
    *,
    blueprint_text: str | None = None,
    target_text: str | None = None,
) -> dict[str, Any]:
    """Inspect current candidate bytes, never a cached review or bounded draft.

    The generated text is the same multi-file artifact used by the handoff
    reviewer. Empty intake scaffolds are not candidates. Contract metadata is
    taken from each source inventory entry, while source statements come from
    the harness manifest (or the campaign QA packet), not the drafting agent.
    """
    if (
        _workflow_kind() != "formalize"
        or _read_text_env("LEANFLOW_FORMALIZATION_STATEMENT_CONTRACT_GATE", "") != "1"
    ):
        return {}
    root = Path(_project_root()).resolve()
    path = Path(active_file)
    if not path.is_absolute():
        path = root / path
    if target_text is None:
        target_text = _formalization_generated_lean_text(str(path))
    if not _declaration_line_index_from_text(_strip_lean_comments_and_strings(target_text)):
        return {}
    findings: list[str] = []
    if blueprint_text is None:
        blueprint = _read_text_env("LEANFLOW_FORMALIZATION_BLUEPRINT", "")
        try:
            blueprint_text = Path(blueprint).read_text(encoding="utf-8") if blueprint else ""
        except OSError:
            blueprint_text = ""
    entries = _blueprint_source_inventory_entries(blueprint_text)
    blocks = _document_formalization_manifest_blocks()
    campaign = _read_text_env("LEANFLOW_FORMALIZATION_CAMPAIGN", "")
    batch = _read_text_env("LEANFLOW_FORMALIZATION_QA_BATCH", "")
    if not blocks and campaign and batch:
        try:
            source = campaign_statement_source(campaign, project_root=root, batch_id=batch)
            if source:
                blocks = [{"label": source.label, "statement": source.statement}]
        except (OSError, ValueError, RuntimeError) as exc:
            findings.append(f"cannot read statement source for contract gate: {exc}")
    # Startup admission may lack a QA packet; accepting declarations may not.
    if not blocks:
        findings.append("candidate has no authoritative statement source for contract gate")
    for block in blocks or [{"label": "candidate", "statement": ""}]:
        label = str(block.get("label", "candidate") or "candidate")
        statement = str(block.get("statement", "") or "").strip()
        if not statement:
            findings.append(f"{label}: authoritative source statement is blank or missing")
        raw = _blueprint_bullet_value(entries.get(label, ""), "Source fidelity contract")
        contract: dict[str, str] = {}
        if raw and raw != "[none]":
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, dict) or any(
                    not isinstance(value, str) for value in parsed.values()
                ):
                    raise ValueError("expected a JSON object of string fields")
                contract = parsed
            except ValueError as exc:
                findings.append(f"{label}: invalid source fidelity contract: {exc}")
        findings.extend(
            f"{label}: {issue}"
            for issue in lint_generated_statement_contract(
                statement, target_text, source_contract=contract
            )
        )
    findings = list(dict.fromkeys(findings))
    if not findings:
        return {}
    return {
        "ok": False,
        "issues": findings,
        "summary": "statement contract gate returned BLOCK: " + "; ".join(findings),
        "failure_stage": "semantic_contract",
        "retry_class": "semantic",
        "review_decision": "BLOCK",
        "review_provider": "deterministic_statement_contract_lint",
        "review_findings": findings,
        "candidate_diagnostics": [
            {"stage": "semantic_contract", "status": "blocked", "diagnostic": finding}
            for finding in findings
        ],
        "final_diagnostic": "; ".join(findings),
    }


def queue_statement_contract_block(
    autonomy_state: dict[str, Any], gate: Mapping[str, Any]
) -> dict[str, Any]:
    """Queue deterministic BLOCK feedback through the fresh-review boundary."""
    result = {
        "provider": gate["review_provider"],
        "reviewer_called": False,
        "gate_decision": "BLOCK",
        "status": "ok",
        "response": json.dumps({"decision": "BLOCK", "findings": gate["review_findings"]}),
    }
    autonomy_state["document_formalization_review_result"] = result
    autonomy_state["document_formalization_review_feedback_message"] = gate["summary"]
    autonomy_state["document_formalization_review_feedback_pending"] = True
    return {"messages": [], "interrupted": False, "verification_review": result}
