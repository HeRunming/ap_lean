"""Cover the bounded MathForm-style statement lane."""

from __future__ import annotations

import hashlib
import json
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest

from leanflow_cli.formalization import bounded_statement_refinement as bounded
from leanflow_cli.formalization import corpus_campaign, corpus_campaign_runner
from leanflow_cli.workflows import verification_providers
from leanflow_cli.workflows.verification_providers import VerificationReviewResult


def _review(response: str, *, cost: float = 0.1) -> VerificationReviewResult:
    return VerificationReviewResult(
        task="test",
        provider="main",
        mode="model",
        response=response,
        status="ok",
        command=[],
        exit_status=0,
        truncated=False,
        response_chars=len(response),
        max_response_chars=10000,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        cost_usd=cost,
    )


def test_statement_draft_rejects_a_proof_body():
    payload = json.dumps(
        {
            "lean_code": "import Mathlib\ntheorem demo : True := by trivial",
            "declarations": ["demo"],
        }
    )
    with pytest.raises(bounded.BoundedStatementRefinementError, match="sorry placeholder"):
        bounded.parse_statement_draft(payload)


def test_statement_draft_normalizes_structured_declaration_names():
    payload = json.dumps(
        {
            "lean_code": "import Mathlib\ntheorem demo : True := by sorry",
            "declarations": [{"name": "demo", "kind": "theorem"}],
        }
    )
    assert bounded.parse_statement_draft(payload).declarations == ("demo",)


@pytest.mark.parametrize(
    "field, tampered", [("source", "tampered source"), ("lean_code", "tampered candidate")]
)
def test_review_statement_evidence_rejects_digest_tampering(tmp_path, monkeypatch, field, tampered):
    evidence_dir = tmp_path / ".leanflow" / "statement-review-evidence"
    evidence_dir.mkdir(parents=True)
    candidate_path = evidence_dir / "candidate.lean"
    source = "For all natural numbers n, prove n + 0 = n."
    proof = ""
    lean_code = "import Mathlib\ntheorem demo (n : Nat) : n + 0 = n := by sorry\n"
    candidate_path.write_text(lean_code, encoding="utf-8")
    source_digest = hashlib.sha256(f"{source}\n{proof}".encode()).hexdigest()
    candidate_digest = hashlib.sha256(lean_code.encode()).hexdigest()
    payload = {
        "source": source,
        "proof": proof,
        "lean_code": lean_code,
        "source_digest": source_digest,
        "candidate_digest": candidate_digest,
        "candidate_path": str(candidate_path.relative_to(tmp_path)),
        "review_prompt": "review",
        "manifest": {"source_digest": source_digest},
    }
    evidence_path = evidence_dir / "evidence.json"
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    payload[field] = tampered
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        bounded,
        "run_model_verification_review",
        lambda **_kwargs: pytest.fail("provider must not be called for tampered evidence"),
    )
    with pytest.raises(bounded.BoundedStatementRefinementError, match="digest mismatch"):
        bounded.review_statement_evidence(evidence_path, provider="custom", model="gpt-6-astra")


def test_persisted_review_prompt_keeps_source_context(tmp_path):
    draft = bounded.StatementDraft(
        lean_code="import Mathlib\ntheorem demo : True := by sorry\n",
        declarations=("demo",),
        source_qualifiers="qualifiers",
        scope_changes="scope",
        proof_notes="notes",
    )
    evidence_path, _, _ = bounded._persist_statement_review_evidence(
        tmp_path,
        batch_id="context-test",
        source_relative="source.json",
        target_relative="Target.lean",
        statement="statement",
        proof="proof",
        draft=draft,
        review_prompt=(
            "SOURCE\nstatement\n\nRESOLVED BOOK REFERENCES\nbook context\n"
            "\nDETERMINISTIC SOURCE-FIDELITY PREFLIGHT\nfidelity\n"
            "\nPRIOR SEMANTIC FEEDBACK\nsemantic feedback"
        ),
    )
    persisted_prompt = json.loads(evidence_path.read_text(encoding="utf-8"))["review_prompt"]
    assert "RESOLVED BOOK REFERENCES\nbook context" in persisted_prompt
    assert "DETERMINISTIC SOURCE-FIDELITY PREFLIGHT\nfidelity" in persisted_prompt
    assert "PRIOR SEMANTIC FEEDBACK\nsemantic feedback" in persisted_prompt


def test_escalation_statement_contract_gate_blocks_invalid_and_accepts_valid():
    invalid = "import Mathlib\ndef p (n : Nat) : Prop := True\n"
    assert "tautological" in " ".join(
        bounded.lint_generated_statement_contract("define a predicate", invalid)
    )
    valid = "import Mathlib\ndef p (n : Nat) : Prop := n = 0\n"
    assert bounded.lint_generated_statement_contract("define a predicate", valid) == ()


def test_statement_contract_lint_allows_a_source_defined_psi2norm():
    code = """import Mathlib
def psi2Norm (f : ℝ → ℝ) : ℝ := 0
theorem demo (f : ℝ → ℝ) : psi2Norm f = 0 := by sorry
"""
    assert bounded.lint_generated_statement_contract("define a Psi-2 norm", code) == ()


def test_campaign_statement_source_admission_missing_source_is_compatible(tmp_path):
    campaign = tmp_path / "campaign.json"
    campaign.write_text(
        json.dumps({"source": "missing.json", "batches": [{"id": "b", "labels": []}]}),
        encoding="utf-8",
    )
    source, issues = bounded.campaign_statement_source_admission(
        campaign, project_root=tmp_path, batch_id="b"
    )
    assert source is None
    assert issues == ()


def test_statement_draft_handles_let_bindings_multiple_theorems_and_lambda_name():
    payload = json.dumps(
        {
            "lean_code": (
                "import Mathlib\n"
                "def helper : ℕ := 1\n"
                "theorem first (n : ℕ) : let x := n; x = n := by sorry\n"
                "theorem second (λ : ℕ) : λ = λ := by sorry\n"
                "theorem limit : Filter.Tendsto id Filter.atTop (𝓝 0) := by sorry\n"
            ),
            "declarations": ["helper", "first", "second", "limit"],
        }
    )

    draft = bounded.parse_statement_draft(payload)

    assert "let x := n" in draft.lean_code
    assert "(coeff : ℕ) : coeff = coeff" in draft.lean_code
    assert "Filter.atTop (nhds 0)" in draft.lean_code
    assert draft.declarations == ("helper", "first", "second", "limit")


def test_statement_draft_ends_theorem_body_at_namespace_end():
    payload = json.dumps(
        {
            "lean_code": (
                "import Mathlib\n"
                "namespace First\n"
                "theorem first : True := by sorry\n"
                "end First\n"
                "namespace Second\n"
                "lemma second : True := by sorry\n"
                "end Second\n"
            ),
            "declarations": ["First.first", "Second.second"],
        }
    )

    draft = bounded.parse_statement_draft(payload)

    assert draft.declarations == ("First.first", "Second.second")


def test_type_context_guidance_blocks_universe_and_numeric_cast_ambiguity():
    guidance = bounded._type_context_guidance()

    assert "never write `Type 0`" in guidance
    assert "`ℝ`, `NNReal`, and `ENNReal` distinct" in guidance
    assert "instead of composite glyphs such as `ℝ≥0` or `ℝ≥0∞`" in guidance
    assert "`(t : ℝ) - (s : ℝ)`" in guidance
    assert (
        "exact declaration name, namespace, signature, and explicit versus implicit binders"
        in guidance
    )
    assert "Do not replace a source-defined norm or predicate" in guidance
    assert "snorm" not in guidance


def test_api_signature_diagnostic_distinguishes_unknown_names_from_argument_mismatches():
    assert "name and namespace" in bounded._api_signature_diagnostic(
        "unknown identifier 'psi2Norm'"
    )
    assert "explicit arguments" in bounded._api_signature_diagnostic(
        "application type mismatch\n  psi2Norm μ f"
    )


def test_statement_draft_ignores_forbidden_words_and_declarations_in_comments():
    payload = json.dumps(
        {
            "lean_code": """import Mathlib
/- The source calls this an axiom.
lemma prose_only : False := by admit
-/
-- theorem prose_only_too : False := by admit
theorem demo : True := by sorry
""",
            "declarations": ["demo"],
        }
    )

    assert bounded.parse_statement_draft(payload).declarations == ("demo",)


def test_statement_draft_rejects_opaque_placeholders():
    payload = json.dumps(
        {
            "lean_code": "import Mathlib\nopaque standardNormalLaw : Nat → Nat\ntheorem demo : True := by sorry",
            "declarations": ["demo"],
        }
    )
    with pytest.raises(bounded.BoundedStatementRefinementError, match="opaque"):
        bounded.parse_statement_draft(payload)


def test_retrieval_queries_ignore_fence_language_marker():
    assert bounded.parse_retrieval_queries("```lean\nconvexHull Euclidean norm\n```") == (
        "convexHull Euclidean norm",
    )


@pytest.mark.parametrize(
    "statement,kind",
    [
        ("(Again – why is this a semidefinite program?)", "conjecture"),
        ("Is this the right rate of decay, or should we expect faster convergence?", "conjecture"),
        ("The constant 2 does not have any special meaning; it can be replaced.", "remark"),
        ("", "theorem"),
    ],
)
def test_statement_scope_preflight_rejects_unformalizable_prose(statement, kind):
    assert bounded.statement_scope_preflight(statement, kind=kind)


@pytest.mark.parametrize(
    "statement,kind",
    [
        # Carries mathematical content, so it stays in scope whatever the kind.
        (r"We agree that $\frac{1}{0}=\infty$ to make conjugate exponents.", "notation"),
        (r"The constant in (5.18) differs by a factor of $\log n$.", "remark"),
        # An asserting kind stays in scope even when stated purely in words.
        ("Every convex body has nonempty interior.", "theorem"),
        ("Prove True.", "exercise"),
    ],
)
def test_statement_scope_preflight_keeps_formalizable_entries(statement, kind):
    assert bounded.statement_scope_preflight(statement, kind=kind) == ""


def test_bounded_statement_lane_skips_out_of_scope_source_without_a_model_call(tmp_path):
    """An unformalizable entry must cost nothing and must not be retried forever."""
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            [{"label": "p91.q1", "kind": "conjecture", "statement": "(Again – why is this so?)"}]
        ),
        encoding="utf-8",
    )
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "source.json",
                "spent_usd": 0.0,
                "budget_usd": 2.0,
                "batches": [
                    {
                        "id": "b",
                        "source_file": "source.json",
                        "labels": ["p91.q1"],
                        "attempts": [],
                        "last_outcome": {"target_file": "Book/Main.lean"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def forbidden_call(**kwargs):
        raise AssertionError("out-of-scope source must not reach a paid provider call")

    outcome = bounded.refine_campaign_statement_bounded(
        campaign_path,
        project_root=tmp_path,
        batch_id="b",
        reserve_usd=1.0,
        provider="main",
        model_call=forbidden_call,
    )

    assert outcome["success"] is False
    assert outcome["failure_stage"] == "out_of_scope"
    assert outcome["failure_class"] == "out_of_scope"
    assert outcome["cost_usd"] == 0.0
    assert outcome["terminal"] is True
    # Terminal, so it is parked rather than re-selected as a statement retry.
    recorded = json.loads(campaign_path.read_text(encoding="utf-8"))
    assert recorded["batches"][0]["status"] == "skipped"
    assert recorded["batches"][0]["agent_status"] == "skipped"
    assert recorded["spent_usd"] == 0.0


def test_terminal_skip_survives_a_campaign_rebuild_and_unblocks_dependents():
    """The skip is re-derived from the ledger, so a rebuild cannot resurrect it."""
    skipped = {
        "stage": "statements",
        "success": False,
        "terminal": True,
        "failure_class": "out_of_scope",
        "reason": "out of scope for formalization: no mathematical content",
        "cost_usd": 0.0,
        "recorded_at": "2026-08-31T00:00:00+00:00",
    }
    campaign = corpus_campaign.build_campaign(
        {
            "source": "source.json",
            "item_count": 2,
            "execution_plan": {"order": ["a", "b"]},
            "source_batches": [
                {"id": "items-a", "labels": ["a"], "selection_kind": "items"},
                {
                    "id": "items-b",
                    "labels": ["b"],
                    "selection_kind": "items",
                    "dependency_labels": ["a"],
                },
            ],
        },
        existing={
            "batches": [
                {"id": "items-a", "labels": ["a"], "attempts": [skipped]},
                {"id": "items-b", "labels": ["b"], "attempts": []},
            ]
        },
    )
    by_id = {batch["id"]: batch for batch in campaign["batches"]}
    assert by_id["items-a"]["status"] == "skipped"
    assert by_id["items-a"]["agent_status"] == "skipped"
    # A dependent batch must become selectable rather than wait on the skip.
    selected = corpus_campaign.next_campaign_batch(campaign, stage="statements")
    assert selected is not None
    assert selected["id"] == "items-b"


@pytest.mark.parametrize("configured", ["", "auto", "local"])
def test_model_verification_provider_never_resolves_to_the_local_verifier(configured):
    """A model stage must not inherit the deterministic ``local`` verifier.

    ``local`` names the deterministic Lean/blueprint checks, not a backend that
    can answer a prompt, and it is the shipped default for
    ``autoformalizer_verification``.  Letting it reach the model dispatcher
    fails the action with "No LLM provider configured ... provider=local".
    """
    assert (
        verification_providers.resolve_model_verification_provider(
            verification_providers.AUTOFORMALIZER_VERIFICATION_TASK, configured or None
        )
        == "main"
    )


def test_model_verification_provider_preserves_an_explicit_backend():
    assert (
        verification_providers.resolve_model_verification_provider(
            verification_providers.AUTOFORMALIZER_VERIFICATION_TASK, "codex"
        )
        == "codex"
    )


def test_retrieval_planner_timeout_is_short_and_bounded(monkeypatch):
    monkeypatch.delenv(bounded.RETRIEVAL_PLANNER_TIMEOUT_ENV, raising=False)
    assert bounded._resolve_retrieval_planner_timeout(None, total_timeout_s=600) == 90
    assert bounded._resolve_retrieval_planner_timeout(30, total_timeout_s=600) == 30
    assert bounded._resolve_retrieval_planner_timeout(600, total_timeout_s=45) == 45


@pytest.mark.parametrize(
    "error",
    ["HTTP 504 Gateway Timeout", "HTTP 524 origin timeout", "Connection error: reset by peer"],
)
def test_retrieval_planner_transient_failure_is_fallback_only(error):
    timeout = VerificationReviewResult(
        task="test",
        provider="custom",
        model="gpt-6-astra",
        mode="model",
        response="",
        status="timeout",
        command=[],
        exit_status=None,
        truncated=False,
        response_chars=0,
        max_response_chars=256,
        timed_out=True,
        error=error,
        failure_class=("reviewer_timeout" if "timeout" in error.casefold() else "provider_error"),
    )
    assert bounded._planner_failure_is_transient(timeout)
    quota = VerificationReviewResult(
        **{
            **timeout.__dict__,
            "status": "unavailable",
            "timed_out": False,
            "error": "provider quota guard: exhausted",
            "failure_class": "budget_limit",
        }
    )
    assert not bounded._planner_failure_is_transient(quota)


def test_transient_retrieval_planner_falls_back_before_generator_and_review(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps([{"label": "b", "question": "For all natural numbers n, prove n + 0 = n."}]),
        encoding="utf-8",
    )
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "source.json",
                "spent_usd": 0.0,
                "budget_usd": 5.0,
                "batches": [
                    {
                        "id": "b",
                        "source_file": "source.json",
                        "labels": ["b"],
                        "attempts": [{"stage": "statements", "success": False}],
                        "last_outcome": {"target_file": "Book/Main.lean"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    planner_timeout = VerificationReviewResult(
        task="autoformalizer_verification",
        provider="planner",
        model="gpt-6-astra",
        mode="model",
        response="",
        status="timeout",
        command=[],
        exit_status=None,
        truncated=False,
        response_chars=0,
        max_response_chars=256,
        timed_out=True,
        error="HTTP 524 origin web server timeout",
        failure_class="reviewer_timeout",
    )
    calls: list[dict] = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return planner_timeout
        if kwargs["task"] == "blueprint_verification":
            return _review("PASS\nFaithful.")
        return _review(
            json.dumps(
                {
                    "lean_code": "import Mathlib\n\ntheorem demo (n : Nat) : n + 0 = n := by sorry\n",
                    "declarations": ["demo"],
                }
            )
        )

    monkeypatch.setattr(
        bounded.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    outcome = bounded.refine_campaign_statement_bounded(
        campaign_path,
        project_root=tmp_path,
        batch_id="b",
        reserve_usd=2.0,
        provider="main",
        planner_provider="planner",
        timeout_s=600,
        model_call=fake_call,
        search_call=lambda *args, **kwargs: SimpleNamespace(to_dict=lambda: {"results": []}),
    )
    assert outcome["success"] is True
    assert outcome["retrieval_planner_fallback"] is True
    assert outcome["retrieval_queries"] == []
    assert calls[0]["timeout_s"] == 90
    assert any(item["stage"] == "planner_unavailable" for item in outcome["candidate_diagnostics"])
    assert [item["task"] for item in calls] == [
        "autoformalizer_verification",
        "autoformalizer_verification",
        "blueprint_verification",
    ]


def test_bounded_statement_lane_routes_default_roles_to_a_real_backend(tmp_path, monkeypatch):
    """Regression: an unset provider must not fail the lane at the planner call.

    With the retrieval fast-path optimization, the first iteration skips the planner
    for fresh batches, so call order is now: generator -> judge (success on first try).
    """
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps([{"question": "For all natural numbers n, prove n + 0 = n."}]),
        encoding="utf-8",
    )
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "source.json",
                "spent_usd": 0.0,
                "budget_usd": 2.0,
                "batches": [
                    {
                        "id": "b",
                        "source_file": "source.json",
                        "attempts": [],
                        "last_outcome": {"target_file": "Book/Main.lean"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        verification_providers,
        "resolve_verification_provider",
        lambda task, explicit=None: "local",
    )
    calls: list[str] = []

    def routed_call(**kwargs):
        calls.append(kwargs["provider"])
        # Optimized call order: generator (1st), judge (2nd), planner (3rd if retry)
        if len(calls) == 1:
            return _review(
                json.dumps(
                    {
                        "lean_code": "import Mathlib\n\ntheorem demo (n : Nat) : n + 0 = n := by sorry\n",
                        "declarations": ["demo"],
                    }
                )
            )
        if len(calls) == 2:
            return _review("PASS\nFaithful.")
        return _review("```\nNat.add_zero\n```")

    monkeypatch.setattr(
        bounded.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    outcome = bounded.refine_campaign_statement_bounded(
        campaign_path,
        project_root=tmp_path,
        batch_id="b",
        reserve_usd=1.0,
        provider="",
        model_call=routed_call,
        search_call=lambda *args, **kwargs: SimpleNamespace(to_dict=lambda: {"results": []}),
    )

    assert outcome["success"] is True
    assert "local" not in calls
    assert set(calls) == {"main"}
    assert outcome["statement_providers"]["planner"] == "main"
    assert outcome["statement_providers"]["judge"] == "main"


def test_bounded_statement_lane_passes_one_explicit_model_to_every_role(tmp_path, monkeypatch):
    """An explicit campaign model must reach planner, generator, and judge."""
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps([{"label": "b", "question": "For all natural numbers n, prove n + 0 = n."}]),
        encoding="utf-8",
    )
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "source.json",
                "spent_usd": 0.0,
                "budget_usd": 5.0,
                "batches": [
                    {
                        "id": "b",
                        "source_file": "source.json",
                        "labels": ["b"],
                        "attempts": [{"stage": "statements", "success": False}],
                        "last_outcome": {"target_file": "Book/Main.lean"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    seen: list[tuple[str, str]] = []

    def fake_call(**kwargs):
        seen.append((kwargs["task"], kwargs["model"]))
        if kwargs["task"] == "blueprint_verification":
            return _review("PASS")
        if kwargs["task"] == "autoformalizer_verification" and len(seen) == 1:
            return _review("```\nNat.add_zero\n```")
        return _review(
            json.dumps(
                {
                    "lean_code": "import Mathlib\n\ntheorem demo (n : Nat) : n + 0 = n := by sorry\n",
                    "declarations": ["demo"],
                }
            )
        )

    monkeypatch.setattr(
        bounded.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    outcome = bounded.refine_campaign_statement_bounded(
        campaign_path,
        project_root=tmp_path,
        batch_id="b",
        reserve_usd=1.0,
        provider="main",
        planner_model="gpt-5.6-sol",
        generator_model="gpt-5.6-sol",
        judge_model="gpt-5.6-sol",
        model_call=fake_call,
        search_call=lambda *args, **kwargs: SimpleNamespace(to_dict=lambda: {"results": []}),
    )

    assert outcome["success"] is True
    assert {model for _task, model in seen} == {"gpt-5.6-sol"}
    assert outcome["statement_models"] == {
        "planner": "gpt-5.6-sol",
        "generator": "gpt-5.6-sol",
        "judge": "gpt-5.6-sol",
    }


def test_bounded_statement_lane_resolves_a_bare_lake_against_the_project_toolchain(
    tmp_path, monkeypatch
):
    """Regression: an elan toolchain outside PATH must not break the compile step.

    ``lake`` under ``<root>/.elan-home/bin`` is on neither PATH nor the caller's
    environment, so a bare ``lake`` raised FileNotFoundError only *after* the
    paid generator call had already been billed.

    With the retrieval fast-path optimization, the first iteration skips the planner
    for fresh batches, so call order is now: generator -> judge (success on first try).
    """
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps([{"question": "For all natural numbers n, prove n + 0 = n."}]),
        encoding="utf-8",
    )
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "source.json",
                "spent_usd": 0.0,
                "budget_usd": 2.0,
                "batches": [
                    {
                        "id": "b",
                        "source_file": "source.json",
                        "attempts": [],
                        "last_outcome": {"target_file": "Book/Main.lean"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    lean_bin = tmp_path / ".elan-home" / "bin"
    lean_bin.mkdir(parents=True)
    for name in ("lake", "lean"):
        executable = lean_bin / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
    monkeypatch.delenv("ELAN_HOME", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    # Optimized call order: generator (1st), judge (2nd), planner (3rd if retry)
    calls = iter(
        [
            _review(
                json.dumps(
                    {
                        "lean_code": "import Mathlib\n\ntheorem demo (n : Nat) : n + 0 = n := by sorry\n",
                        "declarations": ["demo"],
                    }
                )
            ),
            _review("PASS\nFaithful."),
            _review("```\nNat.add_zero\n```"),
        ]
    )
    invoked: dict[str, object] = {}

    def record_run(command, **kwargs):
        invoked["command"] = list(command)
        invoked["path"] = str(dict(kwargs.get("env") or {}).get("PATH", ""))
        invoked["timeout"] = kwargs.get("timeout")
        invoked["remote_timeout"] = dict(kwargs.get("env") or {}).get(
            bounded.REMOTE_LEAN_TIMEOUT_ENV
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bounded.subprocess, "run", record_run)
    outcome = bounded.refine_campaign_statement_bounded(
        campaign_path,
        project_root=tmp_path,
        batch_id="b",
        reserve_usd=1.0,
        provider="main",
        lake_executable="lake",
        model_call=lambda **kwargs: next(calls),
        search_call=lambda *args, **kwargs: SimpleNamespace(to_dict=lambda: {"results": []}),
    )

    assert outcome["success"] is True
    assert invoked["command"][0] == str(lean_bin / "lake")
    assert invoked["timeout"] == bounded.DEFAULT_CANDIDATE_COMPILE_TIMEOUT_S
    assert invoked["remote_timeout"] == "120"
    # ``lake env lean`` re-execs lean from PATH, so the toolchain must be there.
    assert str(lean_bin) in str(invoked["path"])


def test_source_fidelity_preflight_flags_probability_semantics():
    checklist = bounded.source_fidelity_preflight(
        "Let X be a random variable. Prove that its MGF equals its expectation."
    )

    assert "measurability" in checklist
    assert "integrable" in checklist
    assert "Real/NNReal/ENNReal/EReal" in checklist
    assert "pointwise versus almost-everywhere" in checklist


def test_statement_contract_lint_rejects_p207_style_semantic_shortcuts():
    """The contract gate must catch compiling but source-invalid Brownian drafts."""
    draft = bounded.StatementDraft(
        lean_code=(
            "import Mathlib\n"
            "def IsStandardBrownianMotion (X : ℝ → ℝ) (μ : Measure ℝ) : Prop := "
            "X = X ∧ μ = μ\n"
            "theorem reflection (X : ℝ → ℝ) (h : runningSup = bound) : "
            "runningSup = bound := by sorry\n"
        ),
        declarations=("IsStandardBrownianMotion", "reflection"),
        source_qualifiers="none",
        scope_changes="none",
        proof_notes="source proof only",
    )

    issues = bounded.statement_contract_lint(
        "For a standard Brownian motion, the expected running supremum at t₀ "
        "is computed under a probability measure.",
        draft,
    )

    assert "predicate definition is tautological (self-equality)" in issues
    assert "theorem conclusion is repeated verbatim as a hypothesis (circular target)" in issues
    assert any(
        issue.startswith("source contract is missing required fidelity fields:") for issue in issues
    )


def test_statement_contract_lint_requires_probability_and_time_contract_fields():
    statement = (
        "On a probability space with measure μ, for every t₀ ≥ 0, "
        "the expectation of the constant random variable t₀ equals t₀."
    )
    draft = bounded.StatementDraft(
        lean_code=(
            "import Mathlib\n"
            "theorem expectation_const {Ω : Type*} [MeasurableSpace Ω] "
            "(μ : MeasureTheory.Measure Ω) [MeasureTheory.IsProbabilityMeasure μ] "
            "(t₀ : ℝ) (ht₀ : 0 ≤ t₀) : (∫ _ : Ω, t₀ ∂μ) = t₀ := by sorry\n"
        ),
        declarations=("expectation_const",),
        source_qualifiers="none",
        scope_changes="none",
        proof_notes="source proof only",
        source_contract={
            "objects": "constant random variable t₀ and μ",
            "domains": "Ω and t₀ ≥ 0",
            "hypotheses": "μ is a probability measure and t₀ ≥ 0",
            "conclusion": "the expectation of the constant t₀ equals t₀",
            "measure_space": "(Ω, measurableSpace Ω, μ)",
            "measurability": "the constant random variable is measurable",
            "integrability": "the constant is integrable under the finite measure μ",
            # time_domain intentionally omitted to exercise the dedicated gate.
        },
    )

    issues = bounded.statement_contract_lint(statement, draft)

    assert issues == ("source contract is missing required fidelity fields: time_domain",)


def test_statement_contract_lint_accepts_complete_probability_contract():
    statement = (
        "On a probability space with measure μ, for every t₀ ≥ 0, "
        "the expectation of the constant random variable t₀ equals t₀."
    )
    fields = {
        "objects": "constant random variable t₀ and μ",
        "domains": "Ω and t₀ ≥ 0",
        "hypotheses": "μ is a probability measure and t₀ ≥ 0",
        "conclusion": "the expectation of the constant t₀ equals t₀",
        "measure_space": "μ is a probability measure on Ω",
        "measurability": "the constant random variable is measurable",
        "integrability": "the constant is integrable under the finite measure μ",
        "time_domain": "t₀ ≥ 0",
    }
    draft = bounded.StatementDraft(
        lean_code=(
            "import Mathlib\n"
            "theorem expectation_const {Ω : Type*} [MeasurableSpace Ω] "
            "(μ : MeasureTheory.Measure Ω) [MeasureTheory.IsProbabilityMeasure μ] "
            "(t₀ : ℝ) (ht₀ : 0 ≤ t₀) : (∫ _ : Ω, t₀ ∂μ) = t₀ := by sorry\n"
        ),
        declarations=("expectation_const",),
        source_qualifiers="none",
        scope_changes="none",
        proof_notes="source proof only",
        source_contract=fields,
    )

    assert bounded.statement_contract_lint(statement, draft) == ()


def test_parse_statement_draft_preserves_source_contract():
    payload = json.dumps(
        {
            "lean_code": "theorem supremum : True := by sorry\n",
            "declarations": ["supremum"],
            "source_contract": {
                "objects": "X",
                "domains": "0 ≤ t ≤ t₀",
                "hypotheses": "X is Brownian",
                "conclusion": "expectation identity",
                "measure_space": "probability space",
                "measurability": "X measurable",
                "integrability": "supremum integrable",
                "time_domain": "[0, t₀]",
            },
        }
    )

    draft = bounded.parse_statement_draft(payload)

    assert draft.source_contract["time_domain"] == "[0, t₀]"


def test_source_fidelity_preflight_flags_actual_meta_repair_obligation():
    checklist = bounded.source_fidelity_preflight(
        "The following proof is flawed. Fix the argument and prove the corrected conclusion."
    )

    assert "actual corrected theorem" in checklist
    assert "helper lemma is not a faithful substitute" in checklist


def test_source_fidelity_preflight_counts_explicit_subparts():
    checklist = bounded.source_fidelity_preflight(
        "Prove the following.\n(a) First claim.\n(b) Second claim.\n(c) Third claim."
    )

    assert "3 explicit subparts" in checklist
    assert "Cover every subpart" in checklist


def test_reference_context_extraction_recovers_exact_book_declarations():
    book = (
        "Proposition 2.8.1 (Properties). Let X be random.\n"
        "(i) Tail.\n(ii) Moment.\n(iii) MGF.\n"
        "Remark 2.8.2 (Next). This must not be included.\n"
        "Proposition 2.6.1 (Earlier). Let Y be random.\n(i) Other.\n"
    )
    statement = (
        "Prove the equivalence of properties (i)-(iii) in Proposition 2.8.1 "
        "by modifying the proof of Proposition 2.6.1."
    )

    references = bounded.source_references(statement)
    contexts = bounded.extract_reference_contexts_from_text(book, references)

    assert references == ("Proposition 2.8.1", "Proposition 2.6.1")
    assert "(iii) MGF" in contexts["Proposition 2.8.1"]
    assert "Remark 2.8.2" not in contexts["Proposition 2.8.1"]
    assert "Earlier" in contexts["Proposition 2.6.1"]
    assert bounded.source_reference_context_required(statement)


def test_reference_context_extraction_flags_same_number_kind_mismatch():
    contexts = bounded.extract_reference_contexts_from_text(
        "Lemma 7.5.11 (Actual heading). The result.\nRemark 7.5.12 Next.",
        ("Proposition 7.5.11",),
    )

    assert "REFERENCE KIND MISMATCH" in contexts["Proposition 7.5.11"]
    assert "book heading is Lemma 7.5.11" in contexts["Proposition 7.5.11"]
    assert "Exercise 7.5.11" not in bounded.extract_reference_contexts_from_text(
        "Lemma 7.5.11 (Not the exercise).", ("Exercise 7.5.11",)
    )


def test_reference_context_extraction_supports_examples_remarks_and_sections():
    book = (
        "5.1.2 A section heading\nSection body.\n"
        "Example 5.1.3 (Example heading). Example body.\n"
        "Remark 5.1.4 (Remark heading). Remark body.\n"
    )
    statement = "Use Section 5.1.2, Example 5.1.3, and Remark 5.1.4."

    references = bounded.source_references(statement)
    contexts = bounded.extract_reference_contexts_from_text(book, references)

    assert references == ("Section 5.1.2", "Example 5.1.3", "Remark 5.1.4")
    assert "Section body" in contexts["Section 5.1.2"]
    assert "Example body" in contexts["Example 5.1.3"]
    assert "Remark body" in contexts["Remark 5.1.4"]
    assert bounded.source_references("Apply Theorem $8.3.13$.") == ("Theorem 8.3.13",)


def test_reference_resolver_recovers_exercise_from_same_qa_corpus(tmp_path, monkeypatch):
    qa = tmp_path / "qa" / "questions.json"
    qa.parent.mkdir()
    qa.write_text(
        json.dumps(
            [
                {"label": "3.2", "question": "Prove the prerequisite.", "solution": "Hint."},
                {"label": "3.3", "question": "Use Exercise 3.2 to prove the next claim."},
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "book.pdf").write_bytes(b"%PDF-placeholder")
    monkeypatch.setattr(bounded.shutil, "which", lambda _name: "/usr/bin/pdftotext")
    monkeypatch.setattr(
        bounded.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    contexts, missing = bounded.resolve_source_reference_context(
        "Use Exercise 3.2 to prove the next claim.", source_file=qa
    )

    assert missing == ()
    assert "Prove the prerequisite" in contexts["Exercise 3.2"]
    assert "Hint." in contexts["Exercise 3.2"]


def test_source_pdf_text_cache_is_shared_across_calls(tmp_path, monkeypatch):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-placeholder")
    source = tmp_path / "qa" / "questions.json"
    source.parent.mkdir()
    source.write_text("[]", encoding="utf-8")
    (tmp_path / "lakefile.lean").write_text("", encoding="utf-8")
    calls = 0

    def extract_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            returncode=0,
            stdout="Proposition 1.2.3 (Cached). The claim.",
            stderr="",
        )

    monkeypatch.setattr(bounded.shutil, "which", lambda _name: "/usr/bin/pdftotext")
    monkeypatch.setattr(bounded.subprocess, "run", extract_once)

    first = bounded._source_pdf_text(pdf, source_file=source, timeout_s=5)
    second = bounded._source_pdf_text(pdf, source_file=source, timeout_s=5)

    assert first == second
    assert calls == 1
    assert len(list((tmp_path / ".leanflow" / "source-reference-cache").glob("*.txt"))) == 1


def test_bounded_statement_lane_blocks_missing_referenced_source_before_model_call(
    tmp_path, monkeypatch
):
    source = tmp_path / "questions.json"
    source.write_text(
        json.dumps(
            [
                {
                    "label": "2.41",
                    "question": "Prove properties (i)-(iii) in Proposition 2.8.1.",
                }
            ]
        ),
        encoding="utf-8",
    )
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "questions.json",
                "spent_usd": 0.0,
                "budget_usd": 1.0,
                "batches": [
                    {
                        "id": "item",
                        "labels": ["2.41"],
                        "source_file": "questions.json",
                        "status": "pending",
                        "attempts": [],
                        "last_outcome": {"target_file": "Book/Main.lean"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calls = 0

    def forbidden_model_call(**kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("source-context preflight must run before the model")

    monkeypatch.setattr(
        bounded,
        "resolve_source_reference_context",
        lambda *args, **kwargs: ({}, ("Proposition 2.8.1",)),
    )
    outcome = bounded.refine_campaign_statement_bounded(
        campaign_path,
        project_root=tmp_path,
        batch_id="item",
        reserve_usd=0.5,
        provider="main",
        model_call=forbidden_model_call,
    )

    assert calls == 0
    assert outcome["failure_stage"] == "source_context"
    assert outcome["cost_usd"] == 0
    assert outcome["missing_source_references"] == ["Proposition 2.8.1"]
    assert "Supply the original book/PDF or QA context" in outcome["reason"]
    assert "do not invent a proposition" in outcome["reason"]


def test_bounded_target_derivation_matches_document_layout(tmp_path):
    assert (
        bounded.derive_bounded_statement_target(
            tmp_path / "fate-x-work",
            source_file="HDP/source/full/qa/questions.json",
            batch_id="items-0.5",
            selection_kind="items",
        )
        == "FateXWork/Questions/Items05784E1F74/Main.lean"
    )


def test_bounded_statement_lane_compiles_judges_records_and_writes(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "label": "0.1",
                        "question": "For all natural numbers n, prove n + 0 = n.",
                        "proof": "Apply Nat.add_zero.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    target = tmp_path / "Book" / "Main.lean"
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "source.json",
                "spent_usd": 1.0,
                "budget_usd": 5.0,
                "batches": [
                    {
                        "id": "foundation-0.1",
                        "source_file": "source.json",
                        "status": "statement_retry",
                        "attempts": [{"stage": "statements", "success": False, "cost_usd": 1.0}],
                        "last_outcome": {"target_file": "Book/Main.lean"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    responses = iter(
        [
            _review("```\nNat.add_zero\n```"),
            _review(
                json.dumps(
                    {
                        "lean_code": "import Mathlib\n\ntheorem demo (n : Nat) : n + 0 = n := by sorry\n",
                        "declarations": ["demo"],
                        "source_qualifiers": "none",
                        "scope_changes": "none",
                        "proof_notes": "Apply Nat.add_zero.",
                    }
                )
            ),
            _review("PASS\nThe proposition is faithful."),
        ]
    )
    monkeypatch.setattr(
        bounded.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    outcome = bounded.refine_campaign_statement_bounded(
        campaign_path,
        project_root=tmp_path,
        batch_id="foundation-0.1",
        reserve_usd=1.0,
        provider="main",
        model_call=lambda **kwargs: next(responses),
        search_call=lambda *args, **kwargs: SimpleNamespace(
            to_dict=lambda: {
                "results": [{"name": "Nat.add_zero", "statement": "∀ n : Nat, n + 0 = n"}]
            }
        ),
    )

    assert outcome["success"] is True
    assert outcome["final_diagnostic"] == ""
    assert outcome["cost_usd"] == pytest.approx(0.3)
    assert target.read_text(encoding="utf-8").endswith(
        "theorem demo (n : Nat) : n + 0 = n := by sorry\n"
    )
    assert "approved by main verifier" in target.with_name("Blueprint.md").read_text(
        encoding="utf-8"
    )
    campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
    assert campaign["spent_usd"] == pytest.approx(1.3)
    assert campaign["batches"][0]["status"] == "statements_completed"
    assert not list(target.parent.glob("StatementCandidate_*.lean"))


def test_candidate_compile_timeout_is_clamped_to_hard_cap(monkeypatch):
    monkeypatch.setenv("LEANFLOW_STATEMENT_COMPILE_TIMEOUT_S", "9999")
    assert (
        bounded._resolve_candidate_compile_timeout(None, fallback=90)
        == bounded.MAX_CANDIDATE_COMPILE_TIMEOUT_S
    )
    assert bounded._resolve_candidate_compile_timeout(600, fallback=90) == 600.0
    assert bounded._resolve_candidate_compile_timeout(0, fallback=90) == 1.0


def test_candidate_compile_timeout_kills_and_reaps_process_group(monkeypatch):
    signals = []

    class HangingProcess:
        pid = 24680

        def __init__(self):
            self.wait_calls = 0

        def poll(self):
            return None

        def communicate(self, timeout=None):
            if timeout is not None:
                raise bounded.subprocess.TimeoutExpired(["lake"], timeout)
            return "", ""

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise bounded.subprocess.TimeoutExpired(["lake"], timeout)
            return -signal.SIGKILL

        def kill(self):
            signals.append((self.pid, signal.SIGKILL))

    process = HangingProcess()
    popen_kwargs = {}

    def fake_popen(*args, **kwargs):
        popen_kwargs.update(kwargs)
        return process

    monkeypatch.setattr(bounded.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        bounded.os,
        "killpg",
        lambda pid, signum: signals.append((pid, signum)),
    )

    result = bounded._run_candidate_compile(
        ["lake", "env", "lean", "Candidate.lean"],
        cwd=Path("."),
        env={},
        timeout_s=1.0,
    )

    assert result.timed_out is True
    assert result.returncode == 124
    assert popen_kwargs["start_new_session"] is True
    assert signals == [
        (24680, signal.SIGTERM),
        (24680, signal.SIGKILL),
    ]


def test_bounded_statement_lane_records_compile_timeout_diagnostic(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps([{"question": "For all natural numbers n, prove n + 0 = n."}]),
        encoding="utf-8",
    )
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "source.json",
                "spent_usd": 0.0,
                "budget_usd": 2.0,
                "batches": [
                    {
                        "id": "b",
                        "source_file": "source.json",
                        "attempts": [],
                        "last_outcome": {"target_file": "Book/Main.lean"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    compile_call: dict[str, object] = {}

    def fake_compile(*args, **kwargs):
        compile_call.update(kwargs)
        return bounded.CandidateCompileResult(
            returncode=124,
            stderr="Lean compilation timed out after 600 seconds",
            timed_out=True,
            timeout_s=600.0,
        )

    monkeypatch.setattr(bounded, "_run_candidate_compile", fake_compile)

    outcome = bounded.refine_campaign_statement_bounded(
        campaign_path,
        project_root=tmp_path,
        batch_id="b",
        reserve_usd=1.0,
        provider="main",
        compile_timeout_s=600,
        max_iterations=1,
        model_call=lambda **kwargs: _review(
            json.dumps(
                {
                    "lean_code": "import Mathlib\ntheorem demo (n : Nat) : n + 0 = n := by sorry",
                    "declarations": ["demo"],
                }
            )
        ),
    )

    assert outcome["success"] is False
    assert outcome["failure_stage"] == "lean_compilation"
    assert compile_call["timeout_s"] == 600.0
    assert dict(compile_call["env"])[bounded.REMOTE_LEAN_TIMEOUT_ENV] == "600"
    assert outcome["candidate_diagnostics"][-1] == {
        "stage": "lean_compilation",
        "status": "timeout",
        "diagnostic": "Lean compilation timed out after 600 seconds",
        "timeout_s": 600.0,
    }


def test_bounded_statement_lane_stops_after_reserve_is_consumed(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps([{"question": "Prove True."}]), encoding="utf-8")
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "source.json",
                "spent_usd": 0.0,
                "budget_usd": 2.0,
                "batches": [
                    {
                        "id": "b",
                        "source_file": "source.json",
                        "attempts": [],
                        "last_outcome": {"target_file": "Book/Main.lean"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calls = 0

    def expensive_call(**kwargs):
        nonlocal calls
        calls += 1
        return _review("```\nTrue\n```", cost=0.5)

    outcome = bounded.refine_campaign_statement_bounded(
        campaign_path,
        project_root=tmp_path,
        batch_id="b",
        reserve_usd=0.5,
        provider="main",
        model_call=expensive_call,
        search_call=lambda *args, **kwargs: SimpleNamespace(to_dict=lambda: {"results": []}),
    )

    assert calls == 1
    assert outcome["success"] is False
    assert outcome["cost_usd"] == pytest.approx(0.5)


def test_bounded_statement_lane_fails_fast_on_provider_error(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps([{"question": "Prove True."}]), encoding="utf-8")
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "source.json",
                "spent_usd": 0.0,
                "budget_usd": 2.0,
                "batches": [
                    {
                        "id": "b",
                        "source_file": "source.json",
                        "attempts": [],
                        "last_outcome": {"target_file": "Book/Main.lean"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calls = 0

    def failed_call(**kwargs):
        nonlocal calls
        calls += 1
        result = _review("")
        return VerificationReviewResult(
            **{**result.__dict__, "status": "error", "error": "no credentials"}
        )

    outcome = bounded.refine_campaign_statement_bounded(
        campaign_path,
        project_root=tmp_path,
        batch_id="b",
        reserve_usd=1.0,
        provider="main",
        model_call=failed_call,
    )

    assert calls == 1
    assert outcome["success"] is False
    assert outcome["iterations"] == 1


def test_bounded_statement_lane_uses_independent_roles_and_generator_fallback(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            [
                {
                    "question": "For all natural numbers n, prove n + 0 = n.",
                    "proof": "Apply Nat.add_zero.",
                }
            ]
        ),
        encoding="utf-8",
    )
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "source.json",
                "spent_usd": 0.0,
                "budget_usd": 2.0,
                "batches": [
                    {
                        "id": "b",
                        "source_file": "source.json",
                        "attempts": [],
                        "last_outcome": {"target_file": "Book/Main.lean"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def routed_call(**kwargs):
        calls.append((kwargs["provider"], kwargs["task"]))
        if len(calls) == 1:
            return _review("```\nNat.add_zero\n```")
        if len(calls) == 2:
            failed = _review("")
            return VerificationReviewResult(
                **{**failed.__dict__, "status": "unavailable", "error": "endpoint offline"}
            )
        if len(calls) == 3:
            return _review(
                json.dumps(
                    {
                        "lean_code": "import Mathlib\n\ntheorem demo (n : Nat) : n + 0 = n := by sorry\n",
                        "declarations": ["demo"],
                    }
                )
            )
        return _review("PASS\nFaithful.")

    monkeypatch.setattr(
        bounded.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    outcome = bounded.refine_campaign_statement_bounded(
        campaign_path,
        project_root=tmp_path,
        batch_id="b",
        reserve_usd=1.0,
        provider="main",
        planner_provider="planner",
        generator_provider="mathform",
        generator_fallback_provider="gpt-fallback",
        judge_provider="independent-judge",
        model_call=routed_call,
        search_call=lambda *args, **kwargs: SimpleNamespace(to_dict=lambda: {"results": []}),
    )

    assert outcome["success"] is True
    assert [provider for provider, _task in calls] == [
        "planner",
        "mathform",
        "gpt-fallback",
        "independent-judge",
    ]
    assert outcome["statement_providers"] == {
        "planner": "planner",
        "generator": "mathform",
        "generator_fallback": "gpt-fallback",
        "judge": "independent-judge",
    }
    review = (tmp_path / "Book" / "IndependentReview.md").read_text(encoding="utf-8")
    assert "`independent-judge`" in review


def test_bounded_statement_lane_generates_and_compiles_candidate_pool(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps([{"question": "For all natural numbers n, prove n + 0 = n."}]),
        encoding="utf-8",
    )
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "source.json",
                "spent_usd": 0.0,
                "budget_usd": 2.0,
                "batches": [
                    {
                        "id": "b",
                        "source_file": "source.json",
                        "attempts": [
                            {
                                "failure_stage": "semantic_review",
                                "final_diagnostic": "Use EuclideanSpace and require k ≤ n.",
                            }
                        ],
                        "last_outcome": {
                            "target_file": "Book/Main.lean",
                            "failure_stage": "lean_compilation",
                            "final_diagnostic": "Unknown identifier `nhds`.",
                            "review_decision": "BLOCK",
                            "review_findings": ["The function-space norm is sup, not Euclidean."],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    generated = iter(
        [
            json.dumps(
                {
                    "lean_code": "import Mathlib\n\ntheorem bad : MissingName := by sorry\n",
                    "declarations": ["bad"],
                }
            ),
            json.dumps(
                {
                    "lean_code": "import Mathlib\n\ntheorem good (n : Nat) : n + 0 = n := by sorry\n",
                    "declarations": ["good"],
                }
            ),
        ]
    )
    call_kinds = []
    prompts = []

    def model_call(**kwargs):
        call_kinds.append(kwargs["task"])
        prompts.append(kwargs["prompt"])
        if len(call_kinds) == 1:
            return _review("```\nNat.add_zero\n```")
        if kwargs["task"] == "autoformalizer_verification":
            return _review(next(generated))
        return _review("PASS\nFaithful.")

    compile_calls = []

    def compile_candidate(argv, **_kwargs):
        candidate = tmp_path / argv[-1]
        code = candidate.read_text(encoding="utf-8")
        compile_calls.append(code)
        return SimpleNamespace(
            returncode=1 if "MissingName" in code else 0,
            stdout="",
            stderr="unknown identifier" if "MissingName" in code else "",
        )

    monkeypatch.setattr(bounded.subprocess, "run", compile_candidate)
    outcome = bounded.refine_campaign_statement_bounded(
        campaign_path,
        project_root=tmp_path,
        batch_id="b",
        reserve_usd=1.0,
        provider="main",
        candidates_per_iteration=2,
        candidate_workers=2,
        model_call=model_call,
        search_call=lambda *args, **kwargs: SimpleNamespace(to_dict=lambda: {"results": []}),
    )

    assert outcome["success"] is True
    assert outcome["candidate_attempts"] == 2
    assert outcome["candidates_per_iteration"] == 2
    assert outcome["retry_feedback_source"] == "semantic_review+lean_compilation"
    assert "Use EuclideanSpace and require k ≤ n." in prompts[0]
    assert "Use EuclideanSpace and require k ≤ n." in prompts[1]
    assert "Unknown identifier `nhds`." in prompts[0]
    assert "Unknown identifier `nhds`." in prompts[1]
    assert "The function-space norm is sup, not Euclidean." in prompts[0]
    assert "The function-space norm is sup, not Euclidean." in prompts[1]
    assert any(
        "actual typeclass semantics" in prompt and "Use EuclideanSpace and require k ≤ n." in prompt
        for prompt in prompts
    )
    assert any(
        task == bounded.BLUEPRINT_VERIFICATION_TASK
        and "PRIOR SEMANTIC FEEDBACK" in prompt
        and "The function-space norm is sup, not Euclidean." in prompt
        for task, prompt in zip(call_kinds, prompts)
    )
    assert len(compile_calls) == 2
    assert "theorem good (n : Nat) : n + 0 = n := by sorry" in (
        tmp_path / "Book" / "Main.lean"
    ).read_text(encoding="utf-8")
    assert not list((tmp_path / "Book").glob("StatementCandidate_*.lean"))


def test_bounded_statement_lane_rejects_completed_batch_without_model_call(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps([{"question": "Prove True."}]), encoding="utf-8")
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "source.json",
                "spent_usd": 0.0,
                "budget_usd": 2.0,
                "batches": [
                    {
                        "id": "b",
                        "status": "statements_completed",
                        "source_file": "source.json",
                        "attempts": [],
                        "last_outcome": {"target_file": "Book/Main.lean"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(bounded.BoundedStatementRefinementError, match="already"):
        bounded.refine_campaign_statement_bounded(
            campaign_path,
            project_root=tmp_path,
            batch_id="b",
            reserve_usd=1.0,
            provider="openai-codex",
            model_call=lambda **kwargs: pytest.fail("provider must not be called"),
        )


def test_campaign_executor_routes_statement_to_bounded_lane(tmp_path, monkeypatch):
    campaign_path = tmp_path / "campaign.json"
    campaign = {
        "source": "source.json",
        "spent_usd": 0.0,
        "budget_usd": 2.0,
        "batches": [
            {
                "id": "items-0.1",
                "labels": ["0.1"],
                "selection_kind": "items",
                "attempts": [],
            }
        ],
    }
    campaign_path.write_text(json.dumps(campaign), encoding="utf-8")
    observed = {}

    def fake_refine(*args, **kwargs):
        observed.update(kwargs)
        return {"success": True, "exit_code": 0}

    monkeypatch.setattr(corpus_campaign_runner, "refine_campaign_statement_bounded", fake_refine)
    action = corpus_campaign_runner.CampaignAction(
        stage="statements",
        batch_id="items-0.1",
        labels=("0.1",),
        argv=("python", "-m", "leanflow_cli.main", "workflow", "formalize", "source.json"),
    )

    result = corpus_campaign_runner._execute_campaign_action(
        action,
        campaign_path=campaign_path,
        campaign=campaign,
        project_root=tmp_path,
        reserve_usd=0.5,
        provider="openai-codex",
        model="gpt-5.6-terra",
        bounded_statements=True,
        lake_executable="remote-lake",
        statement_compile_timeout_seconds=7.5,
    )

    assert result["success"] is True
    assert observed["batch_id"] == "items-0.1"
    assert observed["lake_executable"] == "remote-lake"
    assert observed["compile_timeout_s"] == 7.5


def test_campaign_executor_escalates_past_the_bounded_lane(tmp_path, monkeypatch):
    """A batch that exhausted the bounded lane must reach the full-tool lane.

    The bounded lane has a 90s retrieval deadline, three iterations, and no view
    outside its own item, so the batches that exhaust it are disproportionately
    the heavily-cited foundations. Parking them without one unbounded attempt
    strands every downstream item that cites them.
    """
    campaign_path = tmp_path / "campaign.json"
    campaign = {
        "source": "source.json",
        "spent_usd": 0.0,
        # Headroom well above the escalated ceiling, so the floor is what
        # binds here rather than the remaining-budget clamp.
        "budget_usd": 50.0,
        "batches": [
            {
                "id": "items-4.4.3",
                "labels": ["4.4.3"],
                "selection_kind": "items",
                "status": corpus_campaign.ESCALATION_STATUS,
                "attempts": [
                    {
                        "stage": "statements",
                        "success": False,
                        "failure_class": "retry_limit",
                        "escalate": True,
                    }
                ],
            }
        ],
    }
    campaign_path.write_text(json.dumps(campaign), encoding="utf-8")

    def refuse(*args, **kwargs):
        raise AssertionError("escalated batch must not re-enter the bounded lane")

    monkeypatch.setattr(corpus_campaign_runner, "refine_campaign_statement_bounded", refuse)

    launched: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        def communicate(self, timeout=None):
            return "", ""

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def fake_popen(argv, **kwargs):
        launched["argv"] = list(argv)
        launched["env"] = dict(kwargs.get("env") or {})
        return FakeProcess()

    monkeypatch.setattr(corpus_campaign_runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        corpus_campaign_runner, "try_zero_cost_proof_preflight", lambda *a, **k: None
    )

    action = corpus_campaign_runner.CampaignAction(
        stage="statements",
        batch_id="items-4.4.3",
        labels=("4.4.3",),
        argv=("python", "-m", "leanflow_cli.main", "workflow", "formalize", "source.json"),
    )

    corpus_campaign_runner._execute_campaign_action(
        action,
        campaign_path=campaign_path,
        campaign=campaign,
        project_root=tmp_path,
        reserve_usd=0.5,
        bounded_statements=True,
    )

    # It must run the standard full-tool formalize subprocess, flagged as escalated.
    assert "formalize" in launched["argv"]
    assert launched["env"]["LEANFLOW_FORMALIZATION_ESCALATED"] == "1"
    # The full agent does not fit the bounded lane's reservation: give the
    # escalated lane a larger floor so one action can finish.
    assert float(launched["env"]["LEANFLOW_ACTION_COST_LIMIT_USD"]) == pytest.approx(12.0)
    # The gate closes on three mechanical conditions the agent was never told
    # about; every BLOCK in the first wave carried the same three findings.
    contract = launched["env"]["LEANFLOW_FORMALIZATION_ESCALATION_CONTRACT"]
    assert "lean_verify(mode=project)" in contract
    assert "root module" in contract
    assert "statement verification status" in contract.lower()
    assert "root module" in contract


def test_escalated_formalize_starts_fresh_instead_of_resuming_review_gate(monkeypatch):
    """Regression: an escalated retry must not resume at the review gate.

    The bounded lane leaves a checkpoint whose blueprint entry is still awaiting
    review, so a plain resume tells the agent to "run only the missing
    deterministic checks and report the pending blocker". That contradicts the
    escalation completion contract: nine escalated attempts reported BLOCK with
    findings like "planner has not drafted Lean declarations in the target file"
    without ever drafting them, because the resume guidance told them not to.
    """
    from leanflow_cli.native import native_runner

    monkeypatch.setattr(native_runner, "_document_formalization_requested", lambda: True)
    monkeypatch.setattr(
        native_runner, "_document_formalization_blueprint_waiting_for_review", lambda: True
    )

    env = {}
    monkeypatch.setattr(
        native_runner, "_read_text_env", lambda name, default="": env.get(name, default)
    )

    # Bounded lane: resuming straight at the gate is correct.
    bounded = native_runner._workflow_startup_guidance("formalize", "formalize Book/Main.lean")
    assert "Resume directly at the independent statement/source review gate" in bounded

    # Escalated lane: the same checkpoint must not short-circuit the drafting turn.
    env["LEANFLOW_FORMALIZATION_ESCALATED"] = "1"
    escalated = native_runner._workflow_startup_guidance("formalize", "formalize Book/Main.lean")
    assert "Resume directly at the independent statement/source review gate" not in escalated
    assert "Load the native formalization contract" in escalated
