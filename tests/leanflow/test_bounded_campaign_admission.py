"""Check bounded worker ownership, compile admission, and explicit wave budgets."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from leanflow_cli.formalization import bounded_statement_refinement as bounded
from leanflow_cli.formalization import corpus_campaign_runner as runner
from leanflow_cli.workflows.verification_providers import VerificationReviewResult


def _campaign(root: Path) -> Path:
    (root / "source.json").write_text(
        json.dumps([{"label": "1.1", "question": "For all natural numbers n, n + 0 = n."}])
    )
    path = root / "campaign.json"
    path.write_text(
        json.dumps(
            {
                "source": "source.json",
                "budget_usd": 100,
                "spent_usd": 0,
                "batches": [
                    {
                        "id": "item",
                        "labels": ["1.1"],
                        "status": "pending",
                        "attempts": [],
                        "last_outcome": {"target_file": "Book/Main.lean"},
                        "lease": {"worker_id": "owner", "stage": "statements"},
                    }
                ],
            }
        )
    )
    return path


def _review(response: str) -> VerificationReviewResult:
    return VerificationReviewResult(
        task="test",
        provider="custom",
        mode="model",
        status="ok",
        response=response,
        command=[],
        exit_status=0,
        truncated=False,
        response_chars=len(response),
        max_response_chars=10000,
    )


def test_executor_forwards_worker_environment_without_mutating_process(tmp_path, monkeypatch):
    path = _campaign(tmp_path)
    observed = {}

    def refine(*args, **kwargs):
        observed.update(kwargs)
        return {"success": False, "exit_code": 2}

    monkeypatch.setattr(runner, "refine_campaign_statement_bounded", refine)
    monkeypatch.setattr(runner, "try_zero_cost_proof_preflight", lambda *a, **k: None)
    before = dict(os.environ)
    runner._execute_campaign_action(
        runner.CampaignAction(
            stage="statements",
            batch_id="item",
            labels=("1.1",),
            argv=("python", "formalize", "source.json"),
        ),
        campaign_path=path,
        campaign=json.loads(path.read_text()),
        project_root=tmp_path,
        reserve_usd=2,
        bounded_statements=True,
        environ={
            "LEANFLOW_CAMPAIGN_WORKER_ID": "owner",
            "LEANFLOW_PROJECT_LEAN_CAPACITY": "2",
        },
    )
    assert observed["environ"]["LEANFLOW_CAMPAIGN_WORKER_ID"] == "owner"
    assert observed["environ"]["LEANFLOW_WORKFLOW_STATE_NAMESPACE"] == "owner"
    assert observed["environ"]["LEANFLOW_PROJECT_LEAN_CAPACITY"] == "2"
    assert dict(os.environ) == before


@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize("reclaim", [False, True])
def test_bounded_compile_gate_and_lease_owner(tmp_path, monkeypatch, warm, reclaim):
    path = _campaign(tmp_path)
    held = 0
    admissions = []
    captured_env = {}
    before = dict(os.environ)

    @contextmanager
    def acquire(root, *, capacity=None):
        nonlocal held
        assert root == tmp_path
        assert str(capacity) == "2"
        held += 1
        admissions.append("lean")
        try:
            yield
        finally:
            held -= 1

    monkeypatch.setattr(bounded, "acquire_project_lean_capacity", acquire, raising=False)

    def compile_candidate(*args, **kwargs):
        assert held == 1
        captured_env.update(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bounded, "_run_candidate_compile", compile_candidate)

    class WarmProbe:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            assert held == 1

        def check(self, *args):
            assert held == 1
            return {"success": True, "has_errors": False}

        def close(self, **kwargs):
            pass

    monkeypatch.setattr(bounded, "RemoteWarmProbe", WarmProbe)
    replies = iter(
        [
            json.dumps(
                {
                    "lean_code": "import Mathlib\ntheorem demo (n : Nat) : n + 0 = n := by sorry\n",
                    "declarations": ["demo"],
                    "source_qualifiers": "none",
                    "scope_changes": "none",
                }
            ),
            "PASS",
        ]
    )

    def model(**kwargs):
        assert held == 0, "waiting for a model must not hold a Lean slot"
        reply = next(replies)
        if reclaim and reply == "PASS":
            current = json.loads(path.read_text())
            current["batches"][0]["lease"]["worker_id"] = "replacement"
            path.write_text(json.dumps(current))
        return _review(reply)

    outcome = bounded.refine_campaign_statement_bounded(
        path,
        project_root=tmp_path,
        batch_id="item",
        reserve_usd=2,
        provider="custom",
        model_call=model,
        warm_remote_probe=warm,
        environ={
            "LEANFLOW_CAMPAIGN_WORKER_ID": "owner",
            "LEANFLOW_WORKFLOW_STATE_NAMESPACE": "owner",
            "LEANFLOW_PROJECT_LEAN_CAPACITY": "2",
        },
    )
    assert outcome["worker_id"] == "owner"
    assert admissions == ["lean"] * (2 if warm else 1)
    assert held == 0
    if not warm:
        assert captured_env["LEANFLOW_WORKFLOW_STATE_NAMESPACE"] == "owner"
        assert captured_env["LEANFLOW_PROJECT_LEAN_CAPACITY"] == "2"
    persisted = json.loads(path.read_text())["batches"][0]
    if reclaim:
        assert persisted["lease"]["worker_id"] == "replacement"
        assert persisted["attempts"] == []
    else:
        assert persisted["attempts"][-1]["worker_id"] == "owner"
        assert "lease" not in persisted
    assert dict(os.environ) == before


@pytest.mark.parametrize("stage", [None, "statements"])
def test_escalation_cannot_raise_explicit_wave_budget(tmp_path, monkeypatch, stage):
    path = _campaign(tmp_path)
    monkeypatch.setattr(runner, "_campaign_has_escalation_pending", lambda campaign: True)
    claims = []

    def lease(*args, **kwargs):
        claims.append(kwargs)
        return []

    monkeypatch.setattr(runner, "lease_next_campaign_actions", lease)
    with pytest.raises(runner.CampaignExecutionBlocked, match="wave budget"):
        runner.execute_campaign_wave(
            path,
            project_root=tmp_path,
            python_executable="python",
            worker_count=2,
            reserve_usd=2,
            wave_budget_usd=4,
            stage=stage,
        )
    assert claims == [], "insufficient wave budget must fail before reserving work"


def test_escalation_wave_with_sufficient_budget_keeps_aggregate_reservations(tmp_path, monkeypatch):
    path = _campaign(tmp_path)
    monkeypatch.setattr(runner, "_campaign_has_escalation_pending", lambda campaign: True)
    claims = []

    def lease(*args, **kwargs):
        claims.append(kwargs)
        return []

    monkeypatch.setattr(runner, "lease_next_campaign_actions", lease)
    runner.execute_campaign_wave(
        path,
        project_root=tmp_path,
        python_executable="python",
        worker_count=2,
        reserve_usd=12,
        wave_budget_usd=24,
        stage="statements",
    )
    assert claims[0]["reserve_usd"] * claims[0]["worker_count"] == 24


@pytest.mark.parametrize("cause", ["budget", "retry_env_mismatch"])
def test_quota_guard_refuses_before_any_model_request(tmp_path, monkeypatch, cause):
    from agent.accounting.provider_quota import ProviderQuotaError

    path = _campaign(tmp_path)
    env = {
        "LEANFLOW_PROVIDER_QUOTA_BUDGET_PATH": str(tmp_path / "quota.json"),
        "LEANFLOW_AUXILIARY_RETRY_COUNT": "0",
        "LEANFLOW_AUXILIARY_TIMEOUT_RETRY_COUNT": "0",
    }
    for name in (
        "LEANFLOW_AUXILIARY_RETRY_COUNT",
        "LEANFLOW_AUXILIARY_TIMEOUT_RETRY_COUNT",
    ):
        monkeypatch.setenv(name, "0" if cause == "budget" else "1")
    observed = []

    def reserve(**kwargs):
        observed.append(kwargs)
        raise ProviderQuotaError("budget exhausted")

    monkeypatch.setattr(bounded, "reserve_provider_request", reserve)
    result = bounded.refine_campaign_statement_bounded(
        path,
        project_root=tmp_path,
        batch_id="item",
        reserve_usd=2,
        provider="custom",
        environ=env,
        model_call=lambda **kwargs: pytest.fail("no paid request allowed"),
    )
    assert result["infrastructure_failure"] is True
    assert result["review_decision"] == ""
    assert "provider quota guard" in result["final_diagnostic"]
    assert len(observed) == (1 if cause == "budget" else 0)


def test_every_bounded_role_reserves_and_settles_before_next_call(tmp_path, monkeypatch):
    from dataclasses import replace

    path = _campaign(tmp_path)
    env = {
        "LEANFLOW_PROVIDER_QUOTA_BUDGET_PATH": str(tmp_path / "quota.json"),
        "LEANFLOW_OPENAI_BASE_URL": "https://api.zcloudapi.com/v1",
        "LEANFLOW_AUXILIARY_RETRY_COUNT": "0",
        "LEANFLOW_AUXILIARY_TIMEOUT_RETRY_COUNT": "0",
    }
    for name in (
        "LEANFLOW_AUXILIARY_RETRY_COUNT",
        "LEANFLOW_AUXILIARY_TIMEOUT_RETRY_COUNT",
    ):
        monkeypatch.setenv(name, "0")
    reserved = []
    settled = []

    class Reservation:
        reservation_id = "reservation"

        def settle(self, **kwargs):
            settled.append(kwargs)
            return {"status": "charged" if kwargs["status"] == "ok" else "unknown"}

    def reserve(**kwargs):
        assert kwargs["base_url"] == "https://api.zcloudapi.com/v1"
        reserved.append(kwargs)
        return Reservation()

    replies = iter(
        [
            _review("```\n```"),
            replace(_review(""), status="unavailable", error="transient"),
            _review(
                json.dumps(
                    {
                        "lean_code": "import Mathlib\ntheorem demo (n : Nat) : n + 0 = n := by sorry",
                        "declarations": ["demo"],
                        "source_qualifiers": "none",
                        "scope_changes": "none",
                    }
                )
            ),
            _review("PASS"),
        ]
    )

    def model(**kwargs):
        assert len(reserved) == len(settled) + 1
        return next(replies)

    monkeypatch.setattr(bounded, "reserve_provider_request", reserve)
    monkeypatch.setattr(
        bounded,
        "_run_candidate_compile",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    result = bounded.refine_campaign_statement_bounded(
        path,
        project_root=tmp_path,
        batch_id="item",
        reserve_usd=2,
        provider="custom",
        planner_provider="custom",
        generator_provider="custom",
        generator_fallback_provider="main",
        environ=env,
        model_call=model,
    )
    assert result["success"] is True
    assert [r["max_output_tokens"] for r in reserved] == [256, 5000, 5000, 2500]
    assert len(settled) == 4
    assert settled[1]["status"] == "unavailable"
    assert len(result["provider_quota_receipts"]) == 4
