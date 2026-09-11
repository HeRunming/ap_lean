"""Regress source-reference and compiler-review failures from the first HDP wave."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from leanflow_cli.formalization import bounded_statement_refinement as bounded
from leanflow_cli.formalization.statement_compilation_evidence import (
    candidate_signature_probe,
    compilation_review_context,
)
from leanflow_cli.workflows.verification_providers import VerificationReviewResult

# Actual source and reviewer excerpt from scale-runs/20260909T103549Z.
GRASSMANNIAN_SOURCE = (
    r"Consider a random subspace $X \sim \operatorname{Unif}(G_{n,m})$ and a function "
    r"$f : G_{n,m} \to \mathbb{R}$. Then the concentration inequality (5.7) holds."
)
EXP_SOURCE = (
    r"For any $n\times n$ symmetric matrices $A$ and $B$, we have "
    r"$$\operatorname{tr}(e^{A+B})\leq \operatorname{tr}(e^A e^B).$$"
)
EXP_CODE = """import Mathlib
theorem golden_thompson_matrix_trace
    (n : ℕ) (A B : Matrix (Fin n) (Fin n) ℝ)
    (hA : A.IsSymm) (hB : B.IsSymm) :
    Matrix.trace (NormedSpace.exp (A + B)) ≤
      Matrix.trace (NormedSpace.exp A * NormedSpace.exp B) := by sorry
"""
BAD_REVIEW = (
    "BLOCK\nNormedSpace.exp takes the scalar field as an explicit argument. "
    "Use NormedSpace.exp ℝ A, not NormedSpace.exp A."
)


@pytest.mark.parametrize(
    "source",
    [
        GRASSMANNIAN_SOURCE,
        "The concentration inequality (5.7) holds for SO(n).",
        "Use equation (5.7).",
        "Then (5.7) holds.",
        r"Apply \eqref{5.7}.",
    ],
)
def test_equation_number_is_a_required_source_reference(source):
    assert bounded.source_references(source) == ("Equation 5.7",)


def test_multiple_equations_resolve_but_local_display_labels_do_not():
    assert bounded.source_references("Use inequalities (5.7) and (5.8).") == (
        "Equation 5.7",
        "Equation 5.8",
    )
    assert bounded.source_references(r"$$x = y \tag{5.7}$$ Prove equation (5.7).") == ()
    assert bounded.source_references(r"\[x = y \quad (5.7)\] Then (5.7) holds.") == ()
    assert bounded.source_references("Take the scalar value (0.5).") == ()


def test_equation_resolver_uses_displayed_formula_not_prose_citation():
    book = (
        "Use inequality (5.7)\n\n"
        "For every 1-Lipschitz f and t > 0,\n"
        "  P(|f(X) - E f(X)| >= t) <= 2 exp(-c n t^2)    (5.7)\n\n"
        "Theorem 5.2.8. Unrelated next theorem.\n"
    )
    result = bounded.extract_reference_contexts_from_text(book, ("Equation 5.7",))
    assert "1-Lipschitz" in result["Equation 5.7"]
    assert "P(|f(X)" in result["Equation 5.7"]
    assert "Unrelated" not in result["Equation 5.7"]
    assert (
        bounded.extract_reference_contexts_from_text(
            "We use concentration inequality (5.7) holds for a different space.", ("Equation 5.7",)
        )
        == {}
    )


def _campaign(tmp_path, source):
    (tmp_path / "source.json").write_text(json.dumps([{"label": "x", "question": source}]))
    path = tmp_path / "campaign.json"
    path.write_text(
        json.dumps(
            {
                "source": "source.json",
                "spent_usd": 0,
                "budget_usd": 10,
                "batches": [
                    {
                        "id": "item",
                        "labels": ["x"],
                        "status": "pending",
                        "attempts": [],
                        "last_outcome": {"target_file": "Book/Main.lean"},
                    }
                ],
            }
        )
    )
    return path


def test_missing_57_stops_actual_grassmannian_case_before_any_model_or_lean(tmp_path, monkeypatch):
    path = _campaign(tmp_path, GRASSMANNIAN_SOURCE)
    monkeypatch.setattr(bounded, "_run_candidate_compile", lambda *a, **k: pytest.fail("no Lean"))
    result = bounded.refine_campaign_statement_bounded(
        path,
        project_root=tmp_path,
        batch_id="item",
        reserve_usd=2,
        provider="custom",
        model_call=lambda **kwargs: pytest.fail("must not spend three rounds guessing (5.7)"),
    )
    assert result["missing_source_references"] == ["Equation 5.7"]
    assert result["failure_stage"] == "source_context"
    assert result["candidate_attempts"] == 0


def test_signature_probe_preserves_original_code_and_uses_only_known_global_apis():
    checked = candidate_signature_probe(EXP_CODE)
    assert checked.startswith(EXP_CODE)
    assert "#check @_root_.NormedSpace.exp" in checked
    assert "#check @_root_.Matrix.trace" in checked
    assert "#check @_root_.A.IsSymm" not in checked


def test_successful_compile_evidence_reaches_judge_and_persisted_retry_prompt(
    tmp_path, monkeypatch
):
    path = _campaign(tmp_path, EXP_SOURCE)
    output = "NormedSpace.exp : {𝔸 : Type u} → [Ring 𝔸] → [TopologicalSpace 𝔸] → 𝔸 → 𝔸\n"
    calls = []

    def compile_candidate(command, **kwargs):
        checked = (tmp_path / command[-1]).read_text()
        assert "#check @_root_.NormedSpace.exp" in checked
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    def model(**kwargs):
        calls.append(kwargs)
        response = (
            json.dumps(
                {
                    "lean_code": EXP_CODE,
                    "declarations": ["golden_thompson_matrix_trace"],
                    "source_qualifiers": "none",
                    "scope_changes": "none",
                }
            )
            if len(calls) == 1
            else BAD_REVIEW
        )
        return VerificationReviewResult(
            task="test",
            provider="custom",
            mode="model",
            response=response,
            status="ok",
            command=[],
            exit_status=0,
            truncated=False,
            response_chars=len(response),
            max_response_chars=10000,
        )

    monkeypatch.setattr(bounded, "_run_candidate_compile", compile_candidate)
    result = bounded.refine_campaign_statement_bounded(
        path,
        project_root=tmp_path,
        batch_id="item",
        reserve_usd=2,
        provider="custom",
        model_call=model,
        max_iterations=1,
    )
    prompt = calls[-1]["prompt"]
    assert "REMOTE LEAN COMPILATION EVIDENCE" in prompt
    assert output.strip() in prompt
    assert hashlib.sha256(EXP_CODE.encode()).hexdigest() in prompt
    assert "Do not issue a semantic BLOCK based only on a guessed typing error" in prompt
    assert result["success"] is False, "compiler facts must never silently override semantic BLOCK"
    assert result["review_decision"] == "BLOCK"
    evidence = json.loads((tmp_path / result["review_evidence"]).read_text())
    assert evidence["lean_code"] == EXP_CODE
    assert evidence["review_prompt"] == prompt


def test_compilation_fact_preserves_independent_semantic_review():
    evidence = compilation_review_context(EXP_CODE, "")
    assert "Compilation PASS does not establish the right mathematics" in evidence
    assert "Keep BLOCK for genuine semantic mismatches" in evidence


def test_hdp_book_in_full_child_is_preferred_over_unrelated_pdf(tmp_path, monkeypatch):
    source = tmp_path / "HDP/source/environments.json"
    source.parent.mkdir(parents=True)
    source.write_text("[]")
    book = source.parent / "full/HDP-2.pdf"
    book.parent.mkdir()
    book.write_bytes(b"PDF")
    (source.parent / "unrelated.pdf").write_bytes(b"OTHER")
    seen = []

    def extract(pdf, **kwargs):
        seen.append(pdf)
        return "Remark 4.7.3 High probability.\nBound holds for u >= 0.\nExample 4.7.4 Next."

    monkeypatch.setattr(bounded, "_source_pdf_text", extract)
    contexts, missing = bounded.resolve_source_reference_context(
        "Prove Remark 4.7.3.",
        source_file=source,
    )
    assert seen == [book]
    assert missing == ()
    assert "u >= 0" in contexts["Remark 4.7.3"]


def test_remark_keeps_same_section_hypotheses():
    book = (
        "The sample covariance is Sigma_m.\n"
        "Theorem 4.7.1 Covariance estimation. Let X be subgaussian and K >= 1.\n"
        "Proof. Derive the estimate.\n"
        "Remark 4.7.2 Sample complexity.\n"
        "Remark 4.7.3 High probability. For u >= 0 the bound holds.\n"
        "Example 4.7.4 Stop.\n"
    )
    context = bounded.extract_reference_contexts_from_text(book, ("Remark 4.7.3",))["Remark 4.7.3"]
    assert "K >= 1" in context
    assert "Sigma_m" in context
    assert "u >= 0" in context
    assert "Example 4.7.4" not in context
