"""Classify failed formalization runs from persisted, inspectable evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from leanflow_cli.workflows.plan_state import Blueprint
from leanflow_cli.workflows.workflow_json_io import read_json_file

FAILURE_CLASSES = (
    "statement",
    "mathematical",
    "library_interface",
    "proof_search",
    "undetermined",
)


def _records(path: Path) -> tuple[dict[str, Any], ...]:
    """Return parseable JSONL records without treating telemetry damage as proof evidence."""
    if not path.is_file():
        return ()
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            records.append(dict(value))
    return tuple(records)


def _evidence_text(summary: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> str:
    """Build a lowercase search surface from bounded persisted evidence."""
    fragments = [json.dumps(summary, ensure_ascii=False, sort_keys=True)]
    fragments.extend(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records)
    return "\n".join(fragments).casefold()


def classify_failure(
    *,
    blueprint: Blueprint,
    summary: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a conservative four-way diagnosis with evidence and caveats.

    The classifier deliberately prefers ``undetermined`` over inferring a
    mathematical or semantic failure from compilation failure alone.
    Operational degradation is reported separately because it can obscure any
    of the four substantive classes.
    """
    text = _evidence_text(summary, records)
    evidence: list[str] = []
    operational: list[str] = []

    if any(token in text for token in ("mcp unavailable", "timeouterror", '"timed_out": true')):
        operational.append("Lean search/proof tooling was unavailable or timed out")

    false_nodes = [node.name or node.id for node in blueprint.nodes if node.status == "false"]
    if false_nodes:
        evidence.append("kernel-backed negation/false graph node: " + ", ".join(false_nodes[:4]))
        return _result("mathematical", "high", evidence, operational)

    statement_markers = (
        "fidelity mismatch",
        "statement wrong",
        "semantic mismatch",
        "vacuous statement",
        "target drift",
    )
    if any(marker in text for marker in statement_markers):
        evidence.append("persisted audit evidence reports statement drift or semantic mismatch")
        return _result("statement", "high", evidence, operational)

    no_direct_library_result = any(
        marker in text
        for marker in (
            "no direct theorem exactly matching",
            "missing library lemma",
            "mathlib gap",
            "library interface gap",
        )
    )
    has_concrete_strategy = any(
        marker in text
        for marker in (
            "recommended_split",
            "hall",
            "double coset",
            "quotient",
            "one-sided transversal",
        )
    )
    if no_direct_library_result and has_concrete_strategy:
        evidence.append(
            "search found primitives/strategy but no theorem matching the composed target"
        )
        return _result("library_interface", "medium", evidence, operational)

    proof_markers = (
        "kernel rejection",
        "type mismatch",
        "unsolved goals",
        "declaration has metavariables",
        "tactic failed",
        "proof-attempt-rejected",
    )
    if any(marker in text for marker in proof_markers):
        evidence.append("a concrete Lean proof candidate reached the verifier and was rejected")
        return _result("proof_search", "medium", evidence, operational)

    evidence.append(
        "persisted artifacts do not yet separate semantic, mathematical, and Lean failures"
    )
    return _result("undetermined", "low", evidence, operational)


def _result(
    failure_class: str,
    confidence: str,
    evidence: Sequence[str],
    operational: Sequence[str],
) -> dict[str, Any]:
    """Build the stable public diagnosis payload."""
    return {
        "failure_class": failure_class,
        "confidence": confidence,
        "evidence": list(evidence),
        "operational_caveats": list(operational),
    }


def score_failure_artifacts(state_root: Path | str) -> dict[str, Any]:
    """Classify one persisted workflow-state directory."""
    root = Path(state_root)
    summary = read_json_file(root / "summary.json")
    blueprint_path = root / "blueprint.json"
    blueprint = (
        Blueprint.from_mapping(read_json_file(blueprint_path))
        if blueprint_path.is_file()
        else Blueprint()
    )
    records = (*_records(root / "journal.jsonl"), *_records(root / "outcomes.jsonl"))
    return {
        "state_root": str(root),
        **classify_failure(
            blueprint=blueprint,
            summary=summary,
            records=records,
        ),
    }
