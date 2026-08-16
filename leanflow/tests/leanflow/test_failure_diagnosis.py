"""Tests for evidence-based formalization failure classification."""

from evals.failure_diagnosis import classify_failure, score_failure_artifacts
from leanflow_cli.workflows.plan_state import Blueprint, GraphNode


def test_false_node_is_mathematical_failure():
    result = classify_failure(
        blueprint=Blueprint(nodes=(GraphNode(id="n", name="bad", status="false"),)),
        summary={},
        records=(),
    )

    assert result["failure_class"] == "mathematical"
    assert result["confidence"] == "high"


def test_statement_audit_takes_priority_over_compile_noise():
    result = classify_failure(
        blueprint=Blueprint(),
        summary={"audit": "semantic mismatch", "diagnostic": "type mismatch"},
        records=(),
    )

    assert result["failure_class"] == "statement"


def test_library_gap_requires_both_search_failure_and_strategy():
    result = classify_failure(
        blueprint=Blueprint(),
        summary={
            "negative_evidence": [
                "No direct theorem exactly matching the target was found.",
                "Use a Hall matching on double cosets.",
            ]
        },
        records=(),
    )

    assert result["failure_class"] == "library_interface"
    assert result["confidence"] == "medium"


def test_compile_rejection_is_proof_search_not_mathematical_failure():
    result = classify_failure(
        blueprint=Blueprint(),
        summary={},
        records=({"event": "proof-attempt-rejected", "reason": "unsolved goals"},),
    )

    assert result["failure_class"] == "proof_search"


def test_problem_three_artifacts_are_classified(tmp_path):
    (tmp_path / "summary.json").write_text(
        '{"negative_evidence":["No direct theorem exactly matching the target was found.",'
        '"One-sided transversal theorems alone are insufficient."]}',
        encoding="utf-8",
    )
    (tmp_path / "outcomes.jsonl").write_text(
        '{"kind":"lean-search","payload":{"query":"double coset quotient"}}\n'
        '{"kind":"lean-inspect","payload":{"diagnostics":"MCP unavailable"}}\n',
        encoding="utf-8",
    )

    result = score_failure_artifacts(tmp_path)

    assert result["failure_class"] == "library_interface"
    assert result["operational_caveats"]
