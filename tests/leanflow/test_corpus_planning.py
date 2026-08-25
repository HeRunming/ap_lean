"""Cover corpus-level dependency and shared-library planning."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from leanflow_cli.formalization import corpus_campaign_runner
from leanflow_cli.formalization.campaign_store import update_campaign_file
from leanflow_cli.formalization.corpus_campaign import (
    build_campaign,
    lease_campaign_batches,
    next_campaign_batch,
    record_campaign_outcome,
)
from leanflow_cli.formalization.corpus_campaign_runner import (
    CampaignAction,
    CampaignExecutionBlocked,
    CampaignModelPolicy,
    accept_agent_reviewed_statement,
    accept_locally_verified_proof,
    accept_locally_verified_statement,
    campaign_economics_report,
    campaign_execution_admitted,
    campaign_marginal_cost_report,
    classify_statement_semantic_risks,
    describe_next_campaign_action,
    execute_campaign_wave,
    execute_next_campaign_action,
    lease_next_campaign_actions,
    plan_next_campaign_action,
    recover_agent_verified_proof,
    refresh_campaign_source_complexity,
    review_existing_agent_statement,
    select_campaign_model,
    try_zero_cost_proof_preflight,
    validate_campaign_action_paths,
)


def test_latest_statement_audit_overrides_an_earlier_pass():
    plan = {
        "source": "book.json",
        "item_count": 1,
        "execution_plan": {"order": ["1"]},
        "source_batches": [{"id": "b", "labels": ["1"]}],
    }
    campaign = build_campaign(plan)
    campaign = record_campaign_outcome(
        campaign, batch_id="b", outcome={"stage": "statements", "success": True}
    )
    campaign = record_campaign_outcome(
        campaign, batch_id="b", outcome={"stage": "statements", "success": False}
    )

    assert campaign["batches"][0]["status"] == "statement_retry"


def test_campaign_outcome_delivery_is_idempotent_by_timestamped_payload():
    plan = {
        "source": "book.json",
        "item_count": 1,
        "execution_plan": {"order": ["1"]},
        "source_batches": [{"id": "b", "labels": ["1"]}],
    }
    outcome = {
        "stage": "proofs",
        "success": False,
        "exit_code": 130,
        "reason": "signal interrupt",
        "cost_usd": 0.25,
        "recorded_at": "2026-08-24T15:01:52+00:00",
    }
    campaign = record_campaign_outcome(build_campaign(plan), batch_id="b", outcome=outcome)
    campaign = record_campaign_outcome(campaign, batch_id="b", outcome=outcome)

    assert campaign["spent_usd"] == 0.25
    assert campaign["batches"][0]["attempts"] == [{**outcome, "failure_class": "proof_incomplete"}]


def test_campaign_reviews_existing_statement_in_bounded_accounted_stage(tmp_path, monkeypatch):
    target = tmp_path / "Book" / "Batch1" / "Main.lean"
    target.parent.mkdir(parents=True)
    target.write_text("import Mathlib\ntheorem demo : True := by sorry\n", encoding="utf-8")
    blueprint = target.with_name("Blueprint.md")
    blueprint.write_text(
        "- Status: pending review\n"
        "- [ ] Run independent statement/source verification review and apply corrections.\n"
        "- [ ] Mark stable theorem/lemma/example `sorry` declarations ready for a user-started prove workflow.\n"
        "- Statement verification status: awaiting independent review\n",
        encoding="utf-8",
    )
    extracted = (
        tmp_path
        / ".leanflow"
        / "workflow-state"
        / "formalization"
        / "book"
        / "batches"
        / "batch-1"
        / "extracted.txt"
    )
    extracted.parent.mkdir(parents=True)
    extracted.write_text("Prove that True.\n", encoding="utf-8")
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "book.json",
                "item_count": 1,
                "spent_usd": 2.0,
                "budget_usd": 10.0,
                "batches": [
                    {
                        "id": "batch-1",
                        "labels": ["1.1"],
                        "status": "statement_retry",
                        "attempts": [{"stage": "statements", "success": False, "cost_usd": 2.0}],
                        "last_outcome": {"target_file": "Book/Batch1/Main.lean"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        corpus_campaign_runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        corpus_campaign_runner,
        "run_model_verification_review",
        lambda **kwargs: SimpleNamespace(
            task="blueprint_verification",
            provider="openai-codex",
            mode="model",
            response="PASS\nFindings:\n- faithful statement",
            status="ok",
            command=[],
            exit_status=None,
            truncated=False,
            response_chars=42,
            max_response_chars=4000,
            timed_out=False,
            model="gpt-5.6-terra",
            error="",
            prompt_tokens=1000,
            completion_tokens=100,
            total_tokens=1100,
            cost_usd=0.013,
        ),
    )

    outcome = review_existing_agent_statement(
        campaign_path,
        project_root=tmp_path,
        batch_id="batch-1",
        reserve_usd=1.0,
        provider="openai-codex",
        model="gpt-5.6-terra",
    )
    updated = json.loads(campaign_path.read_text(encoding="utf-8"))

    assert outcome["success"] is True
    assert outcome["cost_scope"] == "independent_statement_reviewer"
    assert outcome["usage"]["total_tokens"] == 1100
    assert updated["spent_usd"] == pytest.approx(2.013)
    assert updated["batches"][0]["agent_status"] == "statements_completed"
    assert "approved by openai-codex verifier" in blueprint.read_text(encoding="utf-8")
    assert "Verdict: PASS" in target.with_name("IndependentReview.md").read_text(encoding="utf-8")


def test_campaign_leases_distinct_batches_and_recovers_expired_lease():
    campaign = {
        "batches": [
            {"id": "a", "agent_status": "pending", "labels": ["1.1"]},
            {"id": "b", "agent_status": "pending", "labels": ["1.2"]},
            {"id": "c", "agent_status": "pending", "labels": ["1.3"]},
        ]
    }
    now = datetime.now(UTC)
    updated, leased = lease_campaign_batches(
        campaign,
        stage="statements",
        worker_ids=["w1", "w2"],
        ttl_seconds=60,
        now=now,
    )
    assert [item["id"] for item in leased] == ["a", "b"]
    assert next_campaign_batch(updated, stage="statements")["id"] == "c"

    recovered, leased_again = lease_campaign_batches(
        updated,
        stage="statements",
        worker_ids=["w3"],
        ttl_seconds=60,
        now=now + timedelta(minutes=2),
    )
    assert leased_again[0]["id"] == "a"
    assert recovered["batches"][0]["lease"]["worker_id"] == "w3"


def test_campaign_plans_book_foundation_from_its_own_document_source():
    plan = {
        "source": "book/qa/questions.json",
        "item_count": 366,
        "execution_plan": {"order": ["foundation:0.0.2", "0.5"]},
        "dependency_edges": [
            {
                "from": "0.5",
                "to": "foundation:0.0.2",
                "status": "declared_unverified",
            }
        ],
        "source_batches": [
            {
                "id": "foundation-0.0.2",
                "chapter": "0",
                "selection_kind": "document",
                "source_file": "book/foundations/theorem-0.0.2.json",
                "labels": ["foundation:0.0.2"],
            },
            {
                "id": "items-0.5",
                "chapter": "0",
                "selection_kind": "items",
                "labels": ["0.5"],
            },
        ],
    }

    campaign = build_campaign(plan)
    assert campaign["item_count"] == 366
    assert campaign["batches"][0]["source_file"] == ("book/foundations/theorem-0.0.2.json")
    assert next_campaign_batch(campaign, stage="statements")["id"] == "foundation-0.0.2"
    action = plan_next_campaign_action(campaign, python_executable="python")
    assert action is not None
    assert action.batch_id == "foundation-0.0.2"
    assert action.argv == (
        "python",
        "-m",
        "leanflow_cli.main",
        "workflow",
        "formalize",
        "book/foundations/theorem-0.0.2.json",
    )
    validate_campaign_action_paths(action, project_root=".")

    foundation_done = record_campaign_outcome(
        campaign,
        batch_id="foundation-0.0.2",
        outcome={"stage": "statements", "success": True, "provenance": "agent"},
    )
    assert next_campaign_batch(foundation_done, stage="statements")["id"] == "items-0.5"


def test_ready_source_foundation_draft_preempts_unrelated_item_proof():
    campaign = {
        "source": "book/qa/questions.json",
        "batches": [
            {
                "id": "foundation-0.0.2",
                "selection_kind": "document",
                "source_file": "book/foundations/theorem-0.0.2.json",
                "labels": ["foundation:0.0.2"],
                "agent_status": "pending",
            },
            {
                "id": "items-0.4",
                "selection_kind": "items",
                "labels": ["0.4"],
                "agent_status": "statements_completed",
                "last_outcome": {"target_file": "Book/Items04/Main.lean"},
            },
        ],
    }

    action = plan_next_campaign_action(campaign, python_executable="python")
    assert action is not None
    assert action.batch_id == "foundation-0.0.2"
    assert action.stage == "statements"


def test_parallel_claim_is_atomic_and_reserves_total_wave_budget(tmp_path):
    (tmp_path / "book.json").write_text("[]", encoding="utf-8")
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "book.json",
                "spent_usd": 1.0,
                "budget_usd": 5.0,
                "batches": [
                    {"id": "a", "labels": ["1.1"], "status": "pending"},
                    {"id": "b", "labels": ["1.2"], "status": "pending"},
                    {"id": "c", "labels": ["1.3"], "status": "pending"},
                ],
            }
        ),
        encoding="utf-8",
    )
    claims = lease_next_campaign_actions(
        campaign_path,
        worker_count=4,
        python_executable="python",
        reserve_usd=2.0,
    )
    assert [action.batch_id for _, action in claims] == ["a", "b"]
    persisted = json.loads(campaign_path.read_text(encoding="utf-8"))
    assert len([item for item in persisted["batches"] if item.get("lease")]) == 2


def test_parallel_claim_plans_completed_document_foundation_as_proof(tmp_path):
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "book.json",
                "spent_usd": 0,
                "budget_usd": 2,
                "batches": [
                    {
                        "id": "foundation",
                        "selection_kind": "document",
                        "source_file": "foundation.json",
                        "labels": ["foundation:0.0.2"],
                        "status": "statements_completed",
                        "last_outcome": {"target_file": "Book/Foundation/Main.lean"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    claims = lease_next_campaign_actions(
        campaign_path,
        worker_count=1,
        python_executable="python",
        reserve_usd=1,
    )

    assert len(claims) == 1
    assert claims[0][1].stage == "proofs"
    assert claims[0][1].target_file == "Book/Foundation/Main.lean"


def test_campaign_model_policy_escalates_only_non_infrastructure_failures():
    action = CampaignAction("proofs", "a", ("1.1",), ("python",))
    policy = CampaignModelPolicy(
        statement_model="cheap-statements",
        proof_model="cheap-proofs",
        escalation_model="strong",
        escalate_after_failures=2,
    )
    campaign = {
        "batches": [
            {
                "id": "a",
                "attempts": [
                    {
                        "stage": "proofs",
                        "success": False,
                        "failure_class": "infrastructure",
                    },
                    {"stage": "proofs", "success": False},
                ],
            }
        ]
    }
    assert (
        select_campaign_model(campaign, action, fallback_model="fallback", policy=policy)
        == "cheap-proofs"
    )
    campaign["batches"][0]["attempts"].append(
        {"stage": "proofs", "success": False, "failure_class": "budget_limit"}
    )
    assert (
        select_campaign_model(campaign, action, fallback_model="fallback", policy=policy)
        == "cheap-proofs"
    )
    campaign["batches"][0]["attempts"].append(
        {"stage": "proofs", "success": False, "failure_class": "proof_incomplete"}
    )
    assert (
        select_campaign_model(campaign, action, fallback_model="fallback", policy=policy)
        == "strong"
    )


def test_coverage_first_scheduler_drafts_fresh_item_before_costly_proof_retry(tmp_path):
    (tmp_path / "book.json").write_text("[]", encoding="utf-8")
    campaign = {
        "source": "book.json",
        "batches": [
            {
                "id": "hard",
                "labels": ["1.1"],
                "status": "statements_completed",
                "last_outcome": {"target_file": "Book/Hard/Main.lean"},
                "attempts": [{"stage": "proofs", "success": False, "cost_usd": 0.5}],
            },
            {"id": "fresh", "labels": ["1.2"], "status": "pending"},
        ],
    }

    action = plan_next_campaign_action(campaign, python_executable="python")

    assert action is not None
    assert (action.batch_id, action.stage) == ("fresh", "statements")


def test_coverage_first_wave_leases_fresh_proof_then_fresh_statement(tmp_path):
    (tmp_path / "book.json").write_text("[]", encoding="utf-8")
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "book.json",
                "spent_usd": 0,
                "budget_usd": 10,
                "batches": [
                    {
                        "id": "fresh-proof",
                        "labels": ["1.1"],
                        "status": "statements_completed",
                        "last_outcome": {"target_file": "Book/A/Main.lean"},
                        "attempts": [{"stage": "statements", "success": True}],
                    },
                    {
                        "id": "old-proof",
                        "labels": ["1.2"],
                        "status": "statements_completed",
                        "last_outcome": {"target_file": "Book/B/Main.lean"},
                        "attempts": [{"stage": "proofs", "success": False}],
                    },
                    {"id": "fresh-statement", "labels": ["1.3"], "status": "pending"},
                ],
            }
        ),
        encoding="utf-8",
    )

    claims = lease_next_campaign_actions(
        campaign_path,
        worker_count=2,
        python_executable="python",
        reserve_usd=1,
    )

    assert [(action.batch_id, action.stage) for _, action in claims] == [
        ("fresh-proof", "proofs"),
        ("fresh-statement", "statements"),
    ]


def test_campaign_economics_report_separates_fresh_and_hard_work():
    report = campaign_economics_report(
        {
            "spent_usd": 6,
            "completed_batch_count": 2,
            "batches": [
                {"id": "statement", "status": "pending", "attempts": []},
                {"id": "proof", "status": "statements_completed", "attempts": []},
                {
                    "id": "hard",
                    "status": "statements_completed",
                    "attempts": [
                        {
                            "stage": "proofs",
                            "success": False,
                            "failure_class": "proof_incomplete",
                            "cost_usd": 1.2,
                        },
                        {
                            "stage": "proofs",
                            "success": False,
                            "failure_class": "proof_incomplete",
                            "cost_usd": 1.1,
                        },
                    ],
                },
            ],
        }
    )

    assert report["fresh_statements"] == 1
    assert report["fresh_proofs"] == 1
    assert report["proof_retries"] == 1
    assert report["hard_proof_retries"] == 1
    assert report["cost_per_completed_batch_usd"] == 3
    assert report["top_cost_batches"][0]["batch_id"] == "hard"


def test_statement_risk_report_groups_remediation_categories():
    attempt = {
        "stage": "statements",
        "success": False,
        "candidate_diagnostics": [
            {
                "diagnostic": (
                    "BLOCK: X is not measurable, so the Bochner integral is not a genuine "
                    "expectation. ENNReal also changes extended-value edge cases."
                )
            },
            {
                "diagnostic": "The candidate is only an auxiliary lemma, not the actual theorem repair."
            },
        ],
    }

    assert classify_statement_semantic_risks(attempt) == {
        "measurability_integrability",
        "extended_value_semantics",
        "meta_proof_repair",
    }
    report = campaign_economics_report(
        {
            "batches": [
                {"id": "one", "status": "statement_retry", "attempts": [attempt]},
                {"id": "two", "status": "statement_retry", "attempts": [attempt]},
            ]
        }
    )
    assert report["statement_risk_counts"] == {
        "extended_value_semantics": 2,
        "measurability_integrability": 2,
        "meta_proof_repair": 2,
    }


def test_marginal_cost_report_uses_recent_stage_and_complexity_cohorts():
    campaign = {
        "batches": [
            {
                "id": "routine-done",
                "status": "proofs_completed",
                "source_complexity_tier": "routine",
                "attempts": [
                    {
                        "stage": "statements",
                        "success": True,
                        "cost_usd": 0.2,
                        "recorded_at": "2026-08-24T01:00:00Z",
                    },
                    {
                        "stage": "proofs",
                        "success": True,
                        "cost_usd": 0.3,
                        "recorded_at": "2026-08-24T02:00:00Z",
                    },
                ],
            },
            {
                "id": "routine-pending",
                "status": "pending",
                "source_complexity_tier": "routine",
                "attempts": [],
            },
            {
                "id": "complex-statement-ready",
                "status": "statements_completed",
                "source_complexity_tier": "complex",
                "attempts": [
                    {
                        "stage": "proofs",
                        "success": False,
                        "cost_usd": 0.9,
                        "recorded_at": "2026-08-24T03:00:00Z",
                    }
                ],
            },
        ]
    }

    report = campaign_marginal_cost_report(campaign)

    assert report["observed_attempts"] == 3
    assert report["cohorts"]["statements:routine"]["median_cost_usd"] == 0.2
    assert report["cohorts"]["proofs:complex"]["success_rate"] == 0
    assert report["remaining_stage_actions"] == 3
    assert report["forecast_covered_actions"] == 3
    assert report["one_pass_p75_forecast_usd"] == 1.4


def test_zero_cost_proof_preflight_commits_without_model_usage(tmp_path, monkeypatch):
    target = tmp_path / "Book" / "Main.lean"
    target.parent.mkdir(parents=True)
    target.write_text(
        "import Mathlib\n-- retain the word sorry in documentation\ntheorem demo : True := by sorry\n",
        encoding="utf-8",
    )
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "book.json",
                "spent_usd": 2.0,
                "budget_usd": 3.0,
                "batches": [
                    {
                        "id": "item",
                        "status": "statements_completed",
                        "agent_status": "statements_completed",
                        "attempts": [{"stage": "statements", "success": True, "cost_usd": 2.0}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    observed = {}

    def compile_candidate(argv, **kwargs):
        observed["source"] = (
            (tmp_path / "Book" / argv[-1]).read_text(encoding="utf-8")
            if not Path(argv[-1]).is_absolute()
            else Path(argv[-1]).read_text(encoding="utf-8")
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(corpus_campaign_runner.subprocess, "run", compile_candidate)
    action = CampaignAction(
        stage="proofs", batch_id="item", labels=("1",), target_file="Book/Main.lean", argv=()
    )

    outcome = try_zero_cost_proof_preflight(
        campaign_path,
        project_root=tmp_path,
        action=action,
    )

    assert outcome is not None
    assert outcome["cost_usd"] == 0
    assert "(simp; done)" in observed["source"]
    assert "simp_all [" not in observed["source"]
    assert "maxRuleApplications := 100" in observed["source"]
    assert "by sorry" not in target.read_text(encoding="utf-8")
    campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
    assert campaign["spent_usd"] == 2.0
    assert campaign["batches"][0]["status"] == "proofs_completed"


def test_zero_cost_proof_preflight_unfolds_local_definitions(tmp_path, monkeypatch):
    target = tmp_path / "Book" / "Main.lean"
    target.parent.mkdir(parents=True)
    target.write_text(
        "import Mathlib\ndef LocalPredicate : Prop := True\n"
        "theorem demo : LocalPredicate := by sorry\n",
        encoding="utf-8",
    )
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "book.json",
                "batches": [
                    {
                        "id": "item",
                        "status": "statements_completed",
                        "attempts": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    observed = {}

    def compile_candidate(argv, **kwargs):
        observed["source"] = Path(argv[-1]).read_text(encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(corpus_campaign_runner.subprocess, "run", compile_candidate)
    action = CampaignAction(
        stage="proofs", batch_id="item", labels=("1",), target_file="Book/Main.lean", argv=()
    )

    assert (
        try_zero_cost_proof_preflight(campaign_path, project_root=tmp_path, action=action)
        is not None
    )
    assert "simp_all [LocalPredicate]" in observed["source"]


def test_recover_agent_verified_proof_commits_durable_candidate(tmp_path, monkeypatch):
    target = tmp_path / "Book" / "Main.lean"
    target.parent.mkdir(parents=True)
    source = "theorem demo : True := by sorry\n"
    target.write_text(source, encoding="utf-8")
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "book.json",
                "spent_usd": 1,
                "budget_usd": 2,
                "batches": [
                    {
                        "id": "item",
                        "labels": ["1.1"],
                        "status": "statements_completed",
                        "last_outcome": {"target_file": "Book/Main.lean"},
                        "attempts": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    evidence = tmp_path / ".leanflow" / "workflow-state" / "workers" / "w1" / "outcomes.jsonl"
    evidence.parent.mkdir(parents=True)
    evidence.write_text(
        json.dumps(
            {
                "kind": "lean-multi-attempt",
                "timestamp": "2026-01-01T00:00:00Z",
                "payload": {
                    "file_path": str(target),
                    "line": 1,
                    "column": source.index("sorry") + 1,
                    "target_verified": True,
                    "verified_attempts": ["exact True.intro"],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        corpus_campaign_runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    outcome = recover_agent_verified_proof(
        campaign_path,
        project_root=tmp_path,
        batch_id="item",
    )

    assert outcome["success"] is True
    assert outcome["provenance"] == "agent"
    assert target.read_text(encoding="utf-8") == "theorem demo : True := by exact True.intro\n"
    persisted = json.loads(campaign_path.read_text(encoding="utf-8"))
    assert persisted["batches"][0]["agent_status"] == "proofs_completed"


def test_recover_agent_verified_proof_promotes_parent_accepted_equivalent_helper(
    tmp_path, monkeypatch
):
    target = tmp_path / "Book" / "Main.lean"
    target.parent.mkdir(parents=True)
    target.write_text("import Mathlib\ntheorem demo : True := by sorry\n", encoding="utf-8")
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "book.json",
                "batches": [
                    {
                        "id": "item",
                        "status": "statements_completed",
                        "last_outcome": {"target_file": "Book/Main.lean"},
                        "attempts": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    summary = tmp_path / ".leanflow" / "campaign-plan-state" / "item" / "summary.json"
    summary.parent.mkdir(parents=True)
    summary.write_text(
        json.dumps(
            {
                "pending_research_helper_candidate": {
                    "state": "ready_to_integrate",
                    "parent_recheck_status": "accepted",
                    "active_file": str(target),
                    "target_symbol": "demo",
                    "helper_name": "demo_candidate",
                    "declaration": "private theorem demo_candidate : True := by trivial",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        corpus_campaign_runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    outcome = recover_agent_verified_proof(
        campaign_path,
        project_root=tmp_path,
        batch_id="item",
    )

    assert outcome["success"] is True
    assert outcome["cost_usd"] == 0
    recovered = target.read_text(encoding="utf-8")
    assert "theorem demo : True := by trivial" in recovered
    assert "demo_candidate" not in recovered


def test_campaign_store_preserves_concurrent_outcomes(tmp_path):
    campaign_path = tmp_path / "campaign.json"
    plan = {
        "source": "book.json",
        "item_count": 2,
        "execution_plan": {"order": ["1.1", "1.2"]},
        "source_batches": [
            {"id": "a", "chapter": "1", "labels": ["1.1"]},
            {"id": "b", "chapter": "1", "labels": ["1.2"]},
        ],
    }
    campaign_path.write_text(json.dumps(build_campaign(plan)), encoding="utf-8")

    def finish(batch_id):
        def commit(current):
            updated = record_campaign_outcome(
                current,
                batch_id=batch_id,
                outcome={
                    "stage": "statements",
                    "success": True,
                    "cost_usd": 1.0,
                    "provenance": "agent",
                },
            )
            return updated, batch_id

        return update_campaign_file(campaign_path, commit)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert set(pool.map(finish, ["a", "b"])) == {"a", "b"}
    persisted = json.loads(campaign_path.read_text(encoding="utf-8"))
    assert persisted["spent_usd"] == 2.0
    assert persisted["statement_completed_batch_count"] == 2
    assert [len(batch["attempts"]) for batch in persisted["batches"]] == [1, 1]


def test_campaign_wave_launches_distinct_actions_with_stage_model_routing(tmp_path, monkeypatch):
    (tmp_path / "book.json").write_text("[]", encoding="utf-8")
    target = tmp_path / "Book" / "A" / "Main.lean"
    target.parent.mkdir(parents=True)
    target.write_text("import Mathlib\n", encoding="utf-8")
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "book.json",
                "spent_usd": 0,
                "budget_usd": 10,
                "batches": [
                    {
                        "id": "proof",
                        "labels": ["1.1"],
                        "status": "statements_completed",
                        "last_outcome": {"target_file": "Book/A/Main.lean"},
                    },
                    {"id": "statement", "labels": ["1.2"], "status": "pending"},
                ],
            }
        ),
        encoding="utf-8",
    )
    calls = []

    class FakeProcess:
        pid = 123

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    def fake_popen(argv, **kwargs):
        calls.append((tuple(argv), dict(kwargs["env"])))
        return FakeProcess()

    monkeypatch.setattr(corpus_campaign_runner.subprocess, "Popen", fake_popen)
    results = execute_campaign_wave(
        campaign_path,
        project_root=tmp_path,
        python_executable="python",
        worker_count=2,
        reserve_usd=2,
        provider="openai-codex",
        model_policy=CampaignModelPolicy(
            statement_model="cheap-statements", proof_model="cheap-proofs"
        ),
    )

    assert {item["batch_id"] for item in results} == {"proof", "statement"}
    assert {item["model"] for item in results} == {"cheap-proofs", "cheap-statements"}
    assert any(command[-2:] == ("--model", "cheap-proofs") for command, _ in calls)
    assert any(command[-2:] == ("--model", "cheap-statements") for command, _ in calls)
    namespaces = {env["LEANFLOW_WORKFLOW_STATE_NAMESPACE"] for _, env in calls}
    assert len(namespaces) == 2
    assert all(value.startswith("campaign-") for value in namespaces)
    assert all(env["LEANFLOW_DEFER_FIRST_QUEUE_WARMUP"] == "1" for _, env in calls)
    persisted = json.loads(campaign_path.read_text(encoding="utf-8"))
    assert all("lease" not in batch for batch in persisted["batches"])


def test_campaign_wave_splits_explicit_total_budget_across_workers(tmp_path, monkeypatch):
    (tmp_path / "book.json").write_text("[]", encoding="utf-8")
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "book.json",
                "spent_usd": 0,
                "budget_usd": 10,
                "batches": [
                    {"id": "a", "labels": ["1.1"], "status": "pending"},
                    {"id": "b", "labels": ["1.2"], "status": "pending"},
                ],
            }
        ),
        encoding="utf-8",
    )
    observed_limits = []

    def fake_execute(action, **kwargs):
        observed_limits.append(kwargs["reserve_usd"])
        return {"executed": True, "batch_id": action.batch_id, "success": True}

    monkeypatch.setattr(corpus_campaign_runner, "_execute_campaign_action", fake_execute)

    results = execute_campaign_wave(
        campaign_path,
        project_root=tmp_path,
        python_executable="python",
        worker_count=2,
        reserve_usd=1.5,
        wave_budget_usd=1.5,
    )

    assert len(results) == 2
    assert observed_limits == [0.75, 0.75]


from leanflow_cli.formalization.corpus_planning import build_corpus_plan
from leanflow_cli.formalization.corpus_reuse import (
    build_placement_report,
    build_reuse_registry,
    promotion_eligible,
)
from leanflow_cli.formalization.formalization_documents import (
    prepare_formalization_document_context,
)
from leanflow_cli.formalization.promotion_transaction import (
    PromotionTransactionError,
    materialize_promotion_sandbox,
    prepare_promotion_after_images,
)


def test_build_corpus_plan_types_declared_and_candidate_edges():
    plan = build_corpus_plan(
        {
            "theorem_blocks": [
                {
                    "label": "1.1",
                    "statement": "The convex hull of T in ℝ^n is convex.",
                    "uses": [],
                },
                {
                    "label": "1.2",
                    "statement": "A pointwise maximum of convex functions is convex.",
                    "uses": ["1.1"],
                },
            ]
        },
        source_relative="book/questions.json",
        shared_module="Demo.Questions.Shared.Basic",
    )

    assert plan["shared_library"]["candidate_concepts"] == ["convexity"]
    assert plan["dependency_policy"]["candidate_scope"] == "same_chapter"
    assert plan["execution_plan"]["order"] == ["1.1", "1.2"]
    assert plan["execution_plan"]["schedulable"] is True
    assert [module["domain"] for module in plan["library_architecture"]["modules"]] == ["Convexity"]
    assert plan["library_architecture"]["modules"][0]["auto_import"] is False
    assert {
        (edge["from"], edge["to"], edge["kind"], edge["status"])
        for edge in plan["dependency_edges"]
    } == {
        ("1.2", "1.1", "uses_theorem", "declared_unverified"),
        ("1.2", "1.1", "shared_foundation", "candidate"),
    }


def test_corpus_plan_records_source_shape_complexity():
    plan = build_corpus_plan(
        {
            "theorem_blocks": [
                {"label": "1.1", "statement": "Prove that x = x."},
                {
                    "label": "1.2",
                    "statement": (
                        "Prove all parts.\n(a) First identity.\n$$x=x$$\n"
                        "(b) Second identity.\n$$y=y$$\n(c) Third identity."
                    ),
                },
            ],
            "qa_batches": [
                {"id": "complex", "labels": ["1.2"]},
                {"id": "routine", "labels": ["1.1"]},
            ],
        },
        source_relative="book/questions.json",
        shared_module="Demo.Questions.Shared.Basic",
    )
    campaign = build_campaign(plan)
    by_id = {batch["id"]: batch for batch in campaign["batches"]}

    assert by_id["routine"]["source_complexity_tier"] == "routine"
    assert by_id["complex"]["source_subpart_count"] == 3
    assert by_id["complex"]["source_complexity_tier"] == "complex"
    assert next_campaign_batch(campaign, stage="statements")["id"] == "routine"


def test_corpus_plan_defers_meta_proof_repair_exercises():
    plan = build_corpus_plan(
        {
            "theorem_blocks": [
                {
                    "label": "2.16",
                    "statement": "The proof of Theorem 2.4.1 is slightly flawed. Fix this.",
                }
            ],
            "qa_batches": [{"id": "repair", "labels": ["2.16"]}],
        },
        source_relative="book/questions.json",
        shared_module="Demo.Questions.Shared.Basic",
    )

    item = plan["items"][0]
    assert item["source_meta_proof_repair"] == 1
    assert item["source_complexity_tier"] == "complex"
    assert build_campaign(plan)["batches"][0]["source_complexity_tier"] == "complex"


@pytest.mark.parametrize(
    "statement",
    [
        "Deduce the Hoeffding inequality for bounded random variables from the MGF lemma.",
        "Show that any subgaussian random variable satisfying this MGF bound has zero mean.",
        (
            "Demonstrate by example that the bound is asymptotically tight. For every n "
            "find a set in Euclidean space with the required high-dimensional lower bound."
        ),
        "Find normal random variables that are uncorrelated but not independent.",
    ],
)
def test_corpus_plan_marks_short_but_semantically_deep_exercises_complex(statement):
    plan = build_corpus_plan(
        {
            "theorem_blocks": [{"label": "deep", "statement": statement}],
            "qa_batches": [{"id": "deep", "labels": ["deep"]}],
        },
        source_relative="book/questions.json",
        shared_module="Demo.Questions.Shared.Basic",
    )

    item = plan["items"][0]
    assert item["source_semantic_depth_score"] >= 6
    assert item["source_complexity_tier"] == "complex"


def test_fresh_proofs_prefer_fewer_obligations():
    plan = {
        "source": "book.json",
        "item_count": 2,
        "execution_plan": {"order": ["1.1", "1.2"]},
        "source_batches": [
            {"id": "three", "labels": ["1.1"]},
            {"id": "one", "labels": ["1.2"]},
        ],
    }
    campaign = build_campaign(plan)
    campaign = record_campaign_outcome(
        campaign,
        batch_id="three",
        outcome={"stage": "statements", "success": True, "proof_obligations": 3},
    )
    campaign = record_campaign_outcome(
        campaign,
        batch_id="one",
        outcome={"stage": "statements", "success": True, "proof_obligations": 1},
    )

    assert next_campaign_batch(campaign, stage="proofs")["id"] == "one"


def test_campaign_wave_does_not_mix_complex_statements_into_routine_frontier(tmp_path):
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "book.json",
                "budget_usd": 10.0,
                "spent_usd": 0.0,
                "batches": [
                    {
                        "id": "routine",
                        "labels": ["1.1"],
                        "status": "pending",
                        "agent_status": "pending",
                        "source_complexity_tier": "routine",
                    },
                    {
                        "id": "complex",
                        "labels": ["1.2"],
                        "status": "pending",
                        "agent_status": "pending",
                        "source_complexity_tier": "complex",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    claims = lease_next_campaign_actions(
        campaign_path,
        worker_count=2,
        python_executable="python",
        reserve_usd=1.0,
    )

    assert [action.batch_id for _worker, action in claims] == ["routine"]

    leased = json.loads(campaign_path.read_text(encoding="utf-8"))
    leased["batches"][0].pop("lease", None)
    leased["batches"][0]["status"] = "skipped"
    leased["batches"][0]["agent_status"] = "skipped"
    campaign_path.write_text(json.dumps(leased), encoding="utf-8")

    claims = lease_next_campaign_actions(
        campaign_path,
        worker_count=1,
        python_executable="python",
        reserve_usd=1.0,
    )
    assert [action.batch_id for _worker, action in claims] == ["complex"]


def test_campaign_defers_fresh_complex_proof_behind_routine_statement(tmp_path):
    campaign = {
        "source": "book.json",
        "budget_usd": 10.0,
        "spent_usd": 0.0,
        "batches": [
            {
                "id": "deep-proof",
                "labels": ["1.1"],
                "status": "statements_completed",
                "agent_status": "statements_completed",
                "source_complexity_tier": "complex",
                "last_outcome": {"target_file": "Book/Deep.lean"},
                "attempts": [],
            },
            {
                "id": "routine-statement",
                "labels": ["1.2"],
                "status": "pending",
                "agent_status": "pending",
                "source_complexity_tier": "routine",
                "attempts": [],
            },
        ],
    }

    action = plan_next_campaign_action(campaign, python_executable="python")
    assert (action.batch_id, action.stage) == ("routine-statement", "statements")

    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(json.dumps(campaign), encoding="utf-8")
    claims = lease_next_campaign_actions(
        campaign_path,
        worker_count=2,
        python_executable="python",
        reserve_usd=1.0,
    )
    assert [(item.batch_id, item.stage) for _worker, item in claims] == [
        ("routine-statement", "statements")
    ]


def test_refresh_campaign_source_complexity_migrates_legacy_campaign(tmp_path):
    source = tmp_path / "questions.json"
    source.write_text(
        json.dumps(
            [
                {"label": "1.1", "question": "Prove x = x."},
                {
                    "label": "1.2",
                    "question": "Prove all.\n(a) One.\n(b) Two.\n(c) Three.\n$$x=x$$",
                },
            ]
        ),
        encoding="utf-8",
    )
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "questions.json",
                "batches": [
                    {"id": "complex", "labels": ["1.2"]},
                    {"id": "routine", "labels": ["1.1"]},
                ],
            }
        ),
        encoding="utf-8",
    )

    assert refresh_campaign_source_complexity(campaign_path, project_root=tmp_path)
    refreshed = json.loads(campaign_path.read_text(encoding="utf-8"))
    by_id = {batch["id"]: batch for batch in refreshed["batches"]}
    assert by_id["complex"]["source_complexity_tier"] == "complex"
    assert by_id["complex"]["source_subpart_count"] == 3
    assert by_id["routine"]["source_complexity_tier"] == "routine"
    assert not refresh_campaign_source_complexity(campaign_path, project_root=tmp_path)


def test_build_corpus_plan_adds_source_foundation_without_inflating_qa_count():
    plan = build_corpus_plan(
        {
            "theorem_blocks": [
                {"label": "0.5", "statement": "Use approximate Caratheodory."},
                {"label": "6.25", "statement": "Prove the ell-p version."},
            ],
            "qa_batches": [
                {"id": "chapter-0", "chapter": "0", "labels": ["0.5"]},
                {"id": "chapter-6", "chapter": "6", "labels": ["6.25"]},
            ],
            "source_foundations": [
                {
                    "label": "foundation:0.0.2",
                    "chapter": "0",
                    "source_file": "book/foundations/theorem-0.0.2.json",
                    "source_locator": "HDP-2.pdf:physical-page-10",
                    "statement": "Approximate Caratheodory theorem in the Euclidean norm.",
                    "consumers": ["0.5", "6.25"],
                }
            ],
        },
        source_relative="book/qa/questions.json",
        shared_module="Demo.Questions.Shared.Basic",
    )

    assert plan["item_count"] == 2
    assert plan["foundation_count"] == 1
    assert plan["execution_plan"]["order"][0] == "foundation:0.0.2"
    assert plan["source_batches"][0] == {
        "id": "foundation-foundation-0.0.2",
        "chapter": "0",
        "selection_kind": "document",
        "source_file": "book/foundations/theorem-0.0.2.json",
        "labels": ["foundation:0.0.2"],
    }
    hard_edges = {
        (edge["from"], edge["to"])
        for edge in plan["dependency_edges"]
        if edge["status"] == "declared_unverified"
    }
    assert hard_edges == {
        ("0.5", "foundation:0.0.2"),
        ("6.25", "foundation:0.0.2"),
    }

    campaign = build_campaign(plan, existing={"batch_item_limit": 1})
    foundation_batch = campaign["batches"][0]
    assert foundation_batch["selection_kind"] == "document"
    assert foundation_batch["source_file"] == "book/foundations/theorem-0.0.2.json"


def test_prepare_qa_scope_writes_corpus_artifacts_and_shared_module(tmp_path, monkeypatch):
    monkeypatch.setenv("LEANFLOW_FORMALIZATION_PROVENANCE", "agent")
    project = tmp_path / "Demo"
    (project / "Demo").mkdir(parents=True)
    source = project / "book" / "questions.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps(
            [
                {
                    "label": "1.1",
                    "question": "The convex hull of T in R^n is convex.",
                },
                {
                    "label": "1.2",
                    "question": "The pointwise maximum of convex functions is convex.",
                    "dependencies": ["1.1"],
                },
            ]
        ),
        encoding="utf-8",
    )

    context = prepare_formalization_document_context(
        project_root=project,
        cwd=project,
        workflow_args="book/questions.json",
        project_label="Demo",
        qa_items=("1.2",),
    )

    workspace = project / "Demo" / "Questions"
    book_manifest = json.loads((workspace / "book-manifest.json").read_text())
    graph = json.loads((workspace / "dependency-graph.json").read_text())
    assert book_manifest["item_count"] == 2
    assert graph["edges"][0]["status"] == "declared_unverified"
    assert "Candidate edges are retrieval hints" in (workspace / "BookBlueprint.md").read_text()
    assert (workspace / "Shared" / "Basic.lean").is_file()
    assert (workspace / "Shared.lean").read_text() == "import Demo.Questions.Shared.Basic\n"
    assert (workspace / "reuse-registry.json").is_file()
    assert (workspace / "library-architecture.json").is_file()
    assert (workspace / "campaign.json").is_file()
    assert (workspace / "Shared" / "Convexity.lean").is_file()
    assert context.target_lean_path.read_text() == "import Mathlib\n"
    assert context.metadata["corpus_context"]["selected_items"][0]["label"] == "1.2"
    assert (
        context.metadata["corpus_context"]["recommended_shared_modules"][0]["module"]
        == "Demo.Questions.Shared.Convexity"
    )
    rendered_context = context.context_path.read_text()
    assert "## Corpus-Level Reuse Plan" in rendered_context
    assert "candidate` edge" in rendered_context
    assert "Shared declaration registry" in rendered_context
    assert "Recommended shared modules" in rendered_context
    assert "Campaign progress" in rendered_context
    assert "Artifact provenance lane: `agent`" in rendered_context
    assert "Held-out `FateXWork/Gold` proofs are forbidden" in rendered_context
    assert "Shared provenance registry" in rendered_context
    assert "Verified shared declaration interfaces" in rendered_context
    assert "do not read it or the full QA JSON" in rendered_context
    assert "Do not open the batch manifest or whole-book planning artifacts" in rendered_context
    assert "Do not read shared implementation bodies" in rendered_context


def test_campaign_preserves_attempts_and_resumes_next_batch():
    plan = {
        "source": "book.json",
        "item_count": 3,
        "execution_plan": {"order": ["1.1", "1.2", "2.1"]},
        "source_batches": [
            {"id": "chapter-1-batch-1", "chapter": "1", "labels": ["1.2", "1.1"]},
            {"id": "chapter-2-batch-1", "chapter": "2", "labels": ["2.1"]},
        ],
    }
    campaign = build_campaign(plan)
    assert campaign["batches"][0]["labels"] == ["1.1", "1.2"]
    assert next_campaign_batch(campaign)["id"] == "chapter-1-batch-1"

    campaign = record_campaign_outcome(
        campaign,
        batch_id="chapter-1-batch-1",
        outcome={"success": True, "cost_usd": 3.5, "verified_items": 2},
    )
    assert campaign["completed_batch_count"] == 1
    assert campaign["spent_usd"] == 3.5
    assert next_campaign_batch(campaign)["id"] == "chapter-2-batch-1"
    assert campaign["batches"][0]["attempts"][0]["verified_items"] == 2

    budgeted = {**campaign, "budget_usd": 3.5}
    assert next_campaign_batch(budgeted)["id"] == "chapter-2-batch-1"
    summary = describe_next_campaign_action(budgeted, python_executable="python", reserve_usd=0.1)
    assert summary["next_action"]["batch_id"] == "chapter-2-batch-1"
    assert summary["execution_admitted"] is False


def test_campaign_can_reshard_book_batches_into_single_item_actions():
    plan = {
        "source": "book.json",
        "item_count": 3,
        "execution_plan": {"order": ["0.1", "0.2", "1.1"]},
        "source_batches": [
            {"id": "chapter-0-batch-1", "chapter": "0", "labels": ["0.2", "0.1"]},
            {"id": "chapter-1-batch-1", "chapter": "1", "labels": ["1.1"]},
        ],
    }

    campaign = build_campaign(plan, existing={"batch_item_limit": 1, "budget_usd": 7})

    assert campaign["batch_item_limit"] == 1
    assert campaign["budget_usd"] == 7
    assert [batch["id"] for batch in campaign["batches"]] == [
        "items-0.1",
        "items-0.2",
        "items-1.1",
    ]
    assert all(batch["selection_kind"] == "items" for batch in campaign["batches"])


def test_campaign_frontier_waits_for_declared_agent_dependencies():
    plan = {
        "source": "book.json",
        "item_count": 3,
        "execution_plan": {"order": ["1.1", "1.2", "1.3"]},
        "dependency_edges": [
            {"from": "1.2", "to": "1.1", "status": "declared_unverified"},
            {"from": "1.3", "to": "1.1", "status": "candidate"},
        ],
        "source_batches": [
            {"id": "a", "chapter": "1", "labels": ["1.1"]},
            {"id": "b", "chapter": "1", "labels": ["1.2"]},
            {"id": "c", "chapter": "1", "labels": ["1.3"]},
        ],
    }
    campaign = build_campaign(plan)
    assert campaign["batches"][1]["dependency_labels"] == ["1.1"]
    assert campaign["batches"][2]["dependency_labels"] == []
    assert campaign["batches"][2]["soft_dependency_labels"] == ["1.1"]

    leased, frontier = lease_campaign_batches(
        campaign,
        stage="statements",
        worker_ids=["w1", "w2", "w3"],
    )
    assert [item["id"] for item in frontier] == ["a"]

    leased = record_campaign_outcome(
        leased,
        batch_id="a",
        outcome={
            "stage": "statements",
            "success": True,
            "cost_usd": 0,
            "provenance": "agent",
        },
    )
    assert next_campaign_batch(leased, stage="statements")["id"] == "b"
    assert next_campaign_batch(leased, stage="proofs")["id"] == "a"

    leased = record_campaign_outcome(
        leased,
        batch_id="a",
        outcome={
            "stage": "proofs",
            "success": True,
            "cost_usd": 0,
            "provenance": "agent",
        },
    )
    leased = record_campaign_outcome(
        leased,
        batch_id="b",
        outcome={
            "stage": "statements",
            "success": True,
            "cost_usd": 0,
            "provenance": "agent",
        },
    )
    assert next_campaign_batch(leased, stage="proofs")["id"] == "b"


def test_campaign_tracks_statement_and_proof_stages_separately():
    plan = {
        "source": "book.json",
        "item_count": 1,
        "execution_plan": {"order": ["1.1"]},
        "source_batches": [
            {"id": "chapter-1-batch-1", "chapter": "1", "labels": ["1.1"]},
        ],
    }
    campaign = record_campaign_outcome(
        build_campaign(plan),
        batch_id="chapter-1-batch-1",
        outcome={
            "stage": "statements",
            "success": True,
            "cost_usd": 1.0,
            "provenance": "agent",
        },
    )

    assert campaign["statement_completed_batch_count"] == 1
    assert campaign["completed_batch_count"] == 0
    assert next_campaign_batch(campaign, stage="statements") is None
    assert next_campaign_batch(campaign, stage="proofs")["id"] == "chapter-1-batch-1"

    campaign = record_campaign_outcome(
        campaign,
        batch_id="chapter-1-batch-1",
        outcome={
            "stage": "proofs",
            "success": True,
            "cost_usd": 2.0,
            "provenance": "agent",
        },
    )
    assert campaign["completed_batch_count"] == 1
    assert campaign["spent_usd"] == 3.0
    assert campaign["agent_e2e_completed_batch_count"] == 1
    assert campaign["manual_gold_completed_batch_count"] == 0
    assert campaign["batches"][0]["completion_provenance"] == "agent_e2e"
    assert next_campaign_batch(campaign, stage="proofs") is None


def test_campaign_scheduler_deprioritizes_expensive_repeated_batch():
    plan = {
        "source": "book.json",
        "item_count": 3,
        "execution_plan": {"order": ["1.1", "1.2", "1.3"]},
        "source_batches": [
            {"id": "hard", "chapter": "1", "labels": ["1.1"]},
            {"id": "fresh-a", "chapter": "1", "labels": ["1.2"]},
            {"id": "fresh-b", "chapter": "1", "labels": ["1.3"]},
        ],
    }
    campaign = build_campaign(plan)
    for _ in range(3):
        campaign = record_campaign_outcome(
            campaign,
            batch_id="hard",
            outcome={
                "stage": "statements",
                "success": False,
                "reason": "bounded statement refinement exhausted",
                "cost_usd": 1.0,
            },
        )

    assert next_campaign_batch(campaign, stage="statements")["id"] == "fresh-a"
    _leased, claims = lease_campaign_batches(
        campaign,
        stage="statements",
        worker_ids=["w1", "w2"],
    )
    assert [claim["id"] for claim in claims] == ["fresh-a", "fresh-b"]


def test_campaign_failure_taxonomy_is_reported_without_counting_successes():
    plan = {
        "source": "book.json",
        "item_count": 1,
        "execution_plan": {"order": ["1.1"]},
        "source_batches": [{"id": "batch-1", "chapter": "1", "labels": ["1.1"]}],
    }
    campaign = record_campaign_outcome(
        build_campaign(plan),
        batch_id="batch-1",
        outcome={
            "stage": "statements",
            "success": False,
            "reason": "Per-action USD cost limit reached before the next provider request",
            "cost_usd": 1.0,
        },
    )

    assert campaign["failure_class_counts"] == {"budget_limit": 1}
    assert campaign["batches"][0]["last_outcome"]["failure_class"] == "budget_limit"

    assert (
        corpus_campaign_runner.classify_campaign_failure(
            {
                "success": False,
                "failure_class": "statement_generation_incomplete",
                "reason": "runtime failure: Cannot claim workflow live status while verified live owner",
            }
        )
        == "infrastructure"
    )


def test_campaign_runner_closes_proofs_before_drafting_next_batch(tmp_path):
    source = tmp_path / "book.json"
    source.write_text("[]", encoding="utf-8")
    first_target = tmp_path / "Book" / "Batch1" / "Main.lean"
    first_target.parent.mkdir(parents=True)
    first_target.write_text("import Mathlib\n", encoding="utf-8")
    campaign = {
        "source": "book.json",
        "spent_usd": 1.0,
        "budget_usd": 5.0,
        "batches": [
            {
                "id": "batch-1",
                "labels": ["1.1"],
                "status": "statements_completed",
                "last_outcome": {"target_file": "Book/Batch1/Main.lean"},
            },
            {"id": "batch-2", "labels": ["1.2"], "status": "pending"},
        ],
    }

    action = plan_next_campaign_action(campaign, python_executable="python")

    assert action is not None
    assert action.stage == "proofs"
    assert action.batch_id == "batch-1"
    assert action.argv[-2:] == ("prove", "Book/Batch1/Main.lean")
    validate_campaign_action_paths(action, project_root=tmp_path)
    assert campaign_execution_admitted(campaign, reserve_usd=2.0) == (True, "admitted")


def test_campaign_runner_plans_statement_command_and_guards_budget(tmp_path):
    (tmp_path / "book.json").write_text("[]", encoding="utf-8")
    campaign = {
        "source": "book.json",
        "spent_usd": 0,
        "budget_usd": None,
        "batches": [{"id": "batch-1", "labels": ["1.1"], "status": "pending"}],
    }

    action = plan_next_campaign_action(campaign, python_executable="python")

    assert action is not None
    assert action.stage == "statements"
    assert action.argv[-3:] == ("book.json", "--qa-batch", "batch-1")
    validate_campaign_action_paths(action, project_root=tmp_path)
    assert campaign_execution_admitted(campaign, reserve_usd=1.0)[0] is False

    escaped = action.__class__(
        stage=action.stage,
        batch_id=action.batch_id,
        labels=action.labels,
        argv=(*action.argv[:-3], "../book.json", *action.argv[-2:]),
    )
    with pytest.raises(CampaignExecutionBlocked, match="escapes"):
        validate_campaign_action_paths(escaped, project_root=tmp_path)

    summary = describe_next_campaign_action(campaign, python_executable="python", reserve_usd=1.0)
    assert summary["execution_admitted"] is False
    assert summary["next_action"]["batch_id"] == "batch-1"


def test_campaign_executor_runs_only_one_budgeted_action(tmp_path, monkeypatch):
    (tmp_path / "book.json").write_text("[]", encoding="utf-8")
    review = tmp_path / "Book" / "Batch1" / "IndependentReview.md"
    review.parent.mkdir(parents=True)
    review.write_text("Verdict: BLOCK\n", encoding="utf-8")
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "book.json",
                "spent_usd": 1,
                "budget_usd": 5,
                "batches": [
                    {
                        "id": "batch-1",
                        "labels": ["1.1"],
                        "status": "statement_retry",
                        "last_outcome": {
                            "review_decision": "BLOCK",
                            "review_evidence": "Book/Batch1/IndependentReview.md",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calls = []

    class FakeProcess:
        pid = 123

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    def fake_popen(argv, *, cwd, env, stdin, start_new_session):
        calls.append((argv, cwd, env, stdin, start_new_session))
        return FakeProcess()

    monkeypatch.setattr(corpus_campaign_runner.subprocess, "Popen", fake_popen)

    outcome = execute_next_campaign_action(
        campaign_path,
        project_root=tmp_path,
        python_executable="python",
        reserve_usd=2,
        provider="openai-codex",
        model="gpt-5.6-sol",
        environ={"PATH": "/usr/bin"},
    )

    assert outcome == {
        "executed": True,
        "stage": "statements",
        "batch_id": "batch-1",
        "exit_code": 0,
        "success": True,
    }
    assert len(calls) == 1
    assert calls[0][0][3:7] == (
        "workflow",
        "--provider",
        "openai-codex",
        "formalize",
    )
    assert calls[0][0][-2:] == ("--model", "gpt-5.6-sol")
    assert calls[0][2]["LEANFLOW_FORMALIZATION_QA_BATCH"] == "batch-1"
    assert calls[0][2]["LEANFLOW_ACTION_COST_LIMIT_USD"] == "2.0"
    assert calls[0][2]["LEANFLOW_FORMALIZATION_PROVENANCE"] == "agent"
    assert calls[0][2]["LEANFLOW_DISABLE_SOLUTION_RESEARCH"] == "1"
    assert calls[0][2]["LEANFLOW_CLEAN_ROOM_DENY_PATHS"] == "FateXWork/Gold"
    assert calls[0][2]["LEANFLOW_CLEAN_ROOM_DENY_MODULE_PREFIXES"] == "FateXWork.Gold"
    assert calls[0][2]["LEANFLOW_NATIVE_INTERACTIVE"] == "0"
    assert calls[0][2]["LEANFLOW_PLAN_STATE"] == "1"
    assert calls[0][2]["LEANFLOW_PLAN_STATE_DIR"] == str(
        tmp_path / ".leanflow" / "campaign-plan-state" / "batch-1"
    )
    assert calls[0][2]["LEANFLOW_ADVISORY_VERIFICATION_TIMEOUT_S"] == "90"
    assert calls[0][2]["LEANFLOW_FORMALIZATION_REVIEW_EVIDENCE"] == str(review)
    assert calls[0][3] is corpus_campaign_runner.subprocess.DEVNULL


def test_campaign_accepts_reviewed_agent_statement_without_repeating_provider_turn(
    tmp_path, monkeypatch
):
    target = tmp_path / "Book" / "Batch1" / "Main.lean"
    target.parent.mkdir(parents=True)
    target.write_text("import Mathlib\ntheorem demo : True := by sorry\n", encoding="utf-8")
    target.with_name("Blueprint.md").write_text(
        "- Statement verification status: approved by codex verifier\n", encoding="utf-8"
    )
    review = tmp_path / "review.txt"
    review.write_text("PASS\nSource fidelity checked.\n", encoding="utf-8")
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "source": "book.json",
                "item_count": 1,
                "spent_usd": 3.0,
                "budget_usd": 10.0,
                "batches": [
                    {
                        "id": "batch-1",
                        "labels": ["1.1"],
                        "status": "statement_retry",
                        "attempts": [
                            {
                                "stage": "statements",
                                "success": False,
                                "cost_usd": 3.0,
                                "provenance": "agent",
                            }
                        ],
                        "last_outcome": {"target_file": "Book/Batch1/Main.lean"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    class Completed:
        returncode = 0
        stdout = ""
        stderr = "warning: declaration uses `sorry`\n"

    monkeypatch.setattr(corpus_campaign_runner.subprocess, "run", lambda *a, **k: Completed())
    outcome = accept_agent_reviewed_statement(
        campaign_path,
        project_root=tmp_path,
        batch_id="batch-1",
        review_file=review,
    )
    updated = json.loads(campaign_path.read_text(encoding="utf-8"))

    assert outcome["provenance"] == "agent"
    assert outcome["cost_usd"] == 0
    assert outcome["review_evidence"] == "review.txt"
    assert updated["spent_usd"] == 3.0
    assert updated["batches"][0]["agent_status"] == "statements_completed"
    assert next_campaign_batch(updated, stage="proofs")["id"] == "batch-1"


def test_campaign_accepts_locally_verified_statement_without_provider_cost(tmp_path, monkeypatch):
    target = tmp_path / "Book" / "Batch1" / "Main.lean"
    target.parent.mkdir(parents=True)
    target.write_text("import Mathlib\ntheorem demo : True := by sorry\n", encoding="utf-8")
    campaign_path = tmp_path / "campaign.json"
    campaign_path.write_text(
        json.dumps(
            {
                "schema_version": "2",
                "source": "book.json",
                "item_count": 1,
                "batch_count": 1,
                "spent_usd": 2.5,
                "budget_usd": 5,
                "batches": [
                    {
                        "id": "batch-1",
                        "chapter": "1",
                        "labels": ["1.1"],
                        "status": "statement_retry",
                        "attempts": [
                            {
                                "stage": "statements",
                                "success": False,
                                "cost_usd": 2.5,
                            }
                        ],
                        "last_outcome": {"target_file": "Book/Batch1/Main.lean"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    class Completed:
        returncode = 0
        stdout = ""
        stderr = "warning: declaration uses `sorry`\n"

    monkeypatch.setattr(corpus_campaign_runner.subprocess, "run", lambda *a, **k: Completed())

    outcome = accept_locally_verified_statement(
        campaign_path,
        project_root=tmp_path,
        batch_id="batch-1",
    )
    updated = json.loads(campaign_path.read_text(encoding="utf-8"))

    assert outcome["proof_obligations"] == 1
    assert outcome["cost_usd"] == 0
    assert updated["spent_usd"] == 2.5
    assert updated["statement_completed_batch_count"] == 1
    assert updated["batches"][0]["status"] == "statements_completed"

    with pytest.raises(CampaignExecutionBlocked, match="still reports 1 sorry"):
        accept_locally_verified_proof(
            campaign_path,
            project_root=tmp_path,
            batch_id="batch-1",
        )

    class CompletedProof:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(corpus_campaign_runner.subprocess, "run", lambda *a, **k: CompletedProof())
    proof_outcome = accept_locally_verified_proof(
        campaign_path,
        project_root=tmp_path,
        batch_id="batch-1",
    )
    completed_campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
    assert proof_outcome["proof_obligations"] == 0
    assert completed_campaign["spent_usd"] == 2.5
    assert completed_campaign["completed_batch_count"] == 1
    assert completed_campaign["batches"][0]["status"] == "proofs_completed"
    assert completed_campaign["agent_e2e_completed_batch_count"] == 0
    assert completed_campaign["manual_gold_completed_batch_count"] == 1
    assert completed_campaign["batches"][0]["agent_status"] == "pending"
    assert next_campaign_batch(completed_campaign, stage="statements")["id"] == "batch-1"


def test_campaign_runner_supports_single_item_calibration_scope():
    campaign = {
        "source": "book.json",
        "batches": [
            {
                "id": "items-1.2",
                "selection_kind": "items",
                "labels": ["1.2"],
                "status": "pending",
            }
        ],
    }

    action = plan_next_campaign_action(campaign, python_executable="python")

    assert action is not None
    assert action.argv[-3:] == ("book.json", "--qa-items", "1.2")

    rebuilt = build_campaign(
        {
            "source": "book.json",
            "item_count": 1,
            "execution_plan": {"order": ["1.2"]},
            "source_batches": campaign["batches"],
        },
        existing=campaign,
    )
    assert rebuilt["batches"][0]["selection_kind"] == "items"


def test_reuse_registry_detects_duplicates_without_auto_promotion(tmp_path):
    workspace = tmp_path / "Book"
    (workspace / "BatchA").mkdir(parents=True)
    (workspace / "BatchB").mkdir(parents=True)
    declaration = "abbrev RealN (n : ℕ) : Type := EuclideanSpace ℝ (Fin n)\n"
    (workspace / "BatchA" / "Main.lean").write_text(declaration, encoding="utf-8")
    (workspace / "BatchB" / "Main.lean").write_text(declaration, encoding="utf-8")
    registry_path = workspace / "reuse-registry.json"

    registry = build_reuse_registry(workspace, registry_path=registry_path)

    assert len(registry["duplicate_candidates"]) == 1
    candidate = registry["duplicate_candidates"][0]
    assert candidate["name"] == "RealN"
    assert candidate["promotion_eligible"] is False
    assert registry["promotion_contract"]["automatic_source_rewrite"] is False


def test_promotion_gate_requires_project_verification_and_two_consumers():
    assert not promotion_eligible({"verified_consumers": ["1.1", "1.2"], "project_verified": False})
    assert not promotion_eligible({"verified_consumers": ["1.1"], "project_verified": True})
    assert promotion_eligible({"verified_consumers": ["1.1", "1.2"], "project_verified": True})
    assert promotion_eligible({"explicit_source_definition": True, "project_verified": True})


def test_corpus_schedule_reports_cycles_and_keeps_source_order():
    plan = build_corpus_plan(
        {
            "theorem_blocks": [
                {"label": "1.1", "statement": "First.", "uses": ["1.2"]},
                {"label": "1.2", "statement": "Second.", "uses": ["1.1"]},
                {"label": "1.3", "statement": "Third.", "uses": ["missing"]},
            ]
        },
        source_relative="book.json",
        shared_module="Demo.Book.Shared.Basic",
    )

    execution = plan["execution_plan"]
    assert execution["schedulable"] is False
    assert execution["order"] == ["1.3", "1.1", "1.2"]
    assert execution["cycle_labels"] == ["1.1", "1.2"]
    assert execution["unresolved_dependencies"] == [{"item": "1.3", "dependency": "missing"}]


def test_placement_report_routes_reusable_definitions_without_moving_them(tmp_path):
    workspace = tmp_path / "Book"
    target = workspace / "Batch" / "Main.lean"
    target.parent.mkdir(parents=True)
    target.write_text(
        "abbrev RealN (n : ℕ) : Type := EuclideanSpace ℝ (Fin n)\n\n"
        "abbrev convHullInRealN {n : ℕ} (T : Set (RealN n)) := convexHull ℝ T\n",
        encoding="utf-8",
    )
    architecture = {
        "modules": [
            {
                "module": "Demo.Shared.Analysis",
                "routing_concepts": ["euclidean-space", "norm"],
            },
            {
                "module": "Demo.Shared.Convexity",
                "routing_concepts": ["convexity", "convex-hull"],
            },
        ]
    }

    report = build_placement_report(
        workspace,
        architecture=architecture,
        reuse_registry={"duplicate_candidates": [], "promotions": []},
    )

    placements = {item["name"]: item for item in report["placements"]}
    assert placements["RealN"]["recommended_modules"] == ["Demo.Shared.Analysis"]
    assert placements["RealN"]["status"] == "placement_review"
    assert placements["convHullInRealN"]["recommended_modules"] == ["Demo.Shared.Convexity"]
    assert placements["convHullInRealN"]["status"] == "blocked_local_dependency"
    assert placements["convHullInRealN"]["local_dependencies"] == ["RealN"]
    assert placements["convHullInRealN"]["dependency_blockers"][0]["name"] == "RealN"
    assert report["transaction_candidates"] == []
    assert all(item["automatic_move"] is False for item in placements.values())


def test_placement_transaction_requires_registry_approval_and_closed_dependencies(tmp_path):
    workspace = tmp_path / "Book"
    target = workspace / "Batch" / "Main.lean"
    target.parent.mkdir(parents=True)
    target.write_text(
        "abbrev convexUnit : Set ℝ := convexHull ℝ {0}\n",
        encoding="utf-8",
    )
    architecture = {
        "modules": [
            {
                "module": "Demo.Shared.Convexity",
                "routing_concepts": ["convex-hull"],
            }
        ]
    }
    initial = build_placement_report(
        workspace,
        architecture=architecture,
        reuse_registry={"duplicate_candidates": [], "promotions": []},
    )
    digest = initial["placements"][0]["declaration_digest"]

    approved = build_placement_report(
        workspace,
        architecture=architecture,
        reuse_registry={
            "duplicate_candidates": [],
            "promotions": [
                {
                    "name": "convexUnit",
                    "declaration_digest": digest,
                    "verified_consumers": ["1.1", "1.2"],
                    "project_verified": True,
                }
            ],
        },
    )

    assert approved["placements"][0]["status"] == "approved_for_promotion"
    assert approved["transaction_candidates"] == [
        {
            "name": "convexUnit",
            "namespace": [],
            "qualified_name": "convexUnit",
            "declaration_digest": digest,
            "source_file": "Batch/Main.lean",
            "target_modules": ["Demo.Shared.Convexity"],
            "local_dependencies": [],
            "status": "ready_for_verified_candidate_patch",
        }
    ]


def test_promotion_after_images_preserve_namespace_and_do_not_mutate(tmp_path):
    project = tmp_path / "DemoProject"
    workspace = project / "Demo" / "Book"
    source_path = workspace / "Batch" / "Main.lean"
    target_path = workspace / "Shared" / "Convexity.lean"
    source_path.parent.mkdir(parents=True)
    target_path.parent.mkdir(parents=True)
    source_before = (
        "import Mathlib\n\n"
        "namespace Demo\n\n"
        "abbrev convexUnit : Set ℝ := convexHull ℝ {0}\n\n"
        "theorem convexUnit_convex : Convex ℝ convexUnit := convex_convexHull ℝ {0}\n\n"
        "end Demo\n"
    )
    source_path.write_text(source_before, encoding="utf-8")
    target_before = "import Mathlib\n"
    target_path.write_text(target_before, encoding="utf-8")
    architecture = {
        "modules": [
            {
                "module": "Demo.Book.Shared.Convexity",
                "routing_concepts": ["convex-hull"],
            }
        ]
    }
    initial = build_placement_report(
        workspace,
        architecture=architecture,
        reuse_registry={"duplicate_candidates": [], "promotions": []},
    )
    placement = initial["placements"][0]
    approved = build_placement_report(
        workspace,
        architecture=architecture,
        reuse_registry={
            "duplicate_candidates": [],
            "promotions": [
                {
                    "name": placement["name"],
                    "declaration_digest": placement["declaration_digest"],
                    "verified_consumers": ["1.1", "1.2"],
                    "project_verified": True,
                }
            ],
        },
    )

    transaction = approved["transaction_candidates"][0]
    after_images = prepare_promotion_after_images(project, workspace, transaction)

    assert source_path.read_text() == source_before
    assert target_path.read_text() == target_before
    assert "import Demo.Book.Shared.Convexity" in after_images[source_path]
    assert "abbrev convexUnit" not in after_images[source_path]
    assert "theorem convexUnit_convex" in after_images[source_path]
    assert "namespace Demo\n\nabbrev convexUnit" in after_images[target_path]
    assert after_images[target_path].rstrip().endswith("end Demo")

    stale = dict(transaction)
    stale["declaration_digest"] = "stale"
    try:
        prepare_promotion_after_images(project, workspace, stale)
    except PromotionTransactionError as exc:
        assert "stale or ambiguous" in str(exc)
    else:
        raise AssertionError("stale promotion digest should fail")

    sandbox = tmp_path / "promotion-sandbox"
    materialized = materialize_promotion_sandbox(project, after_images, sandbox)
    assert source_path.read_text() == source_before
    assert target_path.read_text() == target_before
    assert (sandbox / source_path.relative_to(project)).read_text() == after_images[source_path]
    assert (sandbox / target_path.relative_to(project)).read_text() == after_images[target_path]
    assert materialized["verification_plan"]["steps"] == [
        "lake env lean Demo/Book/Shared/Convexity.lean",
        "lake env lean Demo/Book/Batch/Main.lean",
        "lake build",
    ]
    assert materialized["verification_plan"]["source_project_mutated"] is False
