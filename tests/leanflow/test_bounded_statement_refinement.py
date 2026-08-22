"""Cover the bounded MathForm-style statement lane."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from leanflow_cli.formalization import bounded_statement_refinement as bounded
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


def test_bounded_statement_lane_compiles_judges_records_and_writes(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps({"questions": [{"label": "0.1", "question": "Prove True.", "proof": "Immediate."}]}),
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
    assert outcome["cost_usd"] == pytest.approx(0.3)
    assert target.read_text(encoding="utf-8").endswith("by sorry\n")
    assert "approved by main verifier" in target.with_name("Blueprint.md").read_text(encoding="utf-8")
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
