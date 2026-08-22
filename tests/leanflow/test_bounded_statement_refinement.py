"""Cover the bounded MathForm-style statement lane."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from leanflow_cli.formalization import bounded_statement_refinement as bounded
from leanflow_cli.formalization import corpus_campaign_runner
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


def test_retrieval_queries_ignore_fence_language_marker():
    assert bounded.parse_retrieval_queries("```lean\nconvexHull Euclidean norm\n```") == (
        "convexHull Euclidean norm",
    )


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
            {"questions": [{"label": "0.1", "question": "Prove True.", "proof": "Immediate."}]}
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
            _review("```\nTrue theorem\n```"),
            _review(
                json.dumps(
                    {
                        "lean_code": "import Mathlib\n\ntheorem demo : True := by sorry\n",
                        "declarations": ["demo"],
                        "source_qualifiers": "none",
                        "scope_changes": "none",
                        "proof_notes": "Immediate.",
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
            to_dict=lambda: {"results": [{"name": "True.intro", "statement": "True"}]}
        ),
    )

    assert outcome["success"] is True
    assert outcome["final_diagnostic"] == ""
    assert outcome["cost_usd"] == pytest.approx(0.3)
    assert target.read_text(encoding="utf-8").endswith("by sorry\n")
    assert "approved by main verifier" in target.with_name("Blueprint.md").read_text(
        encoding="utf-8"
    )
    campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
    assert campaign["spent_usd"] == pytest.approx(1.3)
    assert campaign["batches"][0]["status"] == "statements_completed"
    assert not list(target.parent.glob("StatementCandidate_*.lean"))


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
        json.dumps([{"question": "Prove True.", "proof": "Immediate."}]),
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
            return _review("```\nTrue theorem\n```")
        if len(calls) == 2:
            failed = _review("")
            return VerificationReviewResult(
                **{**failed.__dict__, "status": "unavailable", "error": "endpoint offline"}
            )
        if len(calls) == 3:
            return _review(
                json.dumps(
                    {
                        "lean_code": "import Mathlib\n\ntheorem demo : True := by sorry\n",
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
                    "lean_code": "import Mathlib\n\ntheorem good : True := by sorry\n",
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
            return _review("```\nTrue theorem\n```")
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
    assert len(compile_calls) == 2
    assert (tmp_path / "Book" / "Main.lean").read_text(encoding="utf-8").find("theorem good") >= 0
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
    )

    assert result["success"] is True
    assert observed["batch_id"] == "items-0.1"
    assert observed["lake_executable"] == "remote-lake"
