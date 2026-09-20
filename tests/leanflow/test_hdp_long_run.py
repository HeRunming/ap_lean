"""Pin bounded recovery and the one-time operator allocation for long HDP runs."""

import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def supervisor():
    return runpy.run_path(str(Path(__file__).resolve().parents[2] / "hdp-long-run"))


def result(
    reason="consecutive_infrastructure_failures",
    diagnostic="Error code: 503 - overloaded",
):
    return {
        "finished_at": "done",
        "active": {},
        "stop_reason": reason,
        "quota": {"remaining_quota": 200000},
        "items": [
            {
                "classification": "quota" if reason == "quota" else "infrastructure",
                "new_attempts": [
                    {
                        "final_diagnostic": diagnostic,
                        "failure_class": "infrastructure",
                        "retry_class": "infrastructure",
                    }
                ],
            }
        ],
    }


@pytest.mark.parametrize("code", [500, 502, 503, 504, 524])
def test_transient_provider_errors_have_bounded_cooldowns(supervisor, code):
    r = result(diagnostic=f"Error code: {code}")
    assert supervisor["continuation"](r, retries=0, extended=False) == "cooldown"
    assert supervisor["continuation"](r, retries=3, extended=False) == "stop"


@pytest.mark.parametrize(
    "diagnostic",
    ["Lean compilation failed", "Command interrupted", "", "401 unauthorized"],
)
def test_unknown_failures_never_restart(supervisor, diagnostic):
    assert (
        supervisor["continuation"](result(diagnostic=diagnostic), retries=0, extended=False)
        == "stop"
    )


def test_auxiliary_timeout_is_a_bounded_provider_retry(supervisor):
    r = result(diagnostic="auxiliary call exceeded 600 seconds")
    assert supervisor["continuation"](r, retries=0, extended=False) == "cooldown"


def test_lean_probe_timeout_never_enters_provider_cooldown(supervisor):
    r = result(diagnostic="LeanProbe call exceeded its 120s wall-clock deadline")
    assert supervisor["continuation"](r, retries=0, extended=False) == "stop"


def test_missing_receipt_can_retry_only_after_explicit_reconciliation(supervisor):
    r = result()
    r["items"][0]["new_attempts"] = []
    r["items"][0]["receipt_recovery"] = {
        "safe_to_retry": True,
        "lease_released": True,
        "target_unchanged": True,
    }
    assert supervisor["continuation"](r, retries=0, extended=False) == "continue"
    r["items"][0]["receipt_recovery"]["safe_to_retry"] = False
    assert supervisor["continuation"](r, retries=0, extended=False) == "stop"


def test_only_local_reservation_shortfall_can_allocate_once(supervisor):
    r = result(
        "quota",
        "provider quota guard: provider quota budget does not cover the request reservation",
    )
    assert supervisor["continuation"](r, retries=0, extended=False) == "extend"
    assert supervisor["continuation"](r, retries=0, extended=True) == "stop"
    r["quota"]["halt_reason"] = "reservation overrun"
    assert supervisor["continuation"](r, retries=0, extended=False) == "stop"
    assert (
        supervisor["continuation"](
            result("quota", "provider balance insufficient"), retries=0, extended=False
        )
        == "stop"
    )


def test_active_or_unreleased_workers_block_restart(supervisor):
    r = result()
    r["active"] = {"batch": 123}
    assert supervisor["continuation"](r, retries=0, extended=False) == "stop"


def test_missing_receipt_never_restarts(supervisor):
    r = result()
    r["items"][0]["new_attempts"] = []
    assert supervisor["continuation"](r, retries=0, extended=False) == "stop"
    r["active"] = {}
    r["items"][0]["lease_after"] = {"worker_id": "still-owned"}
    assert supervisor["continuation"](r, retries=0, extended=False) == "stop"


def test_supervisor_keeps_deadline_across_cooldown_and_quota_rollover(
    supervisor, tmp_path, monkeypatch
):
    from agent.accounting import provider_quota_budget

    clock = [0.0]
    options = SimpleNamespace(
        log_dir=tmp_path,
        hours=1,
        budget=tmp_path / "old.json",
        extension_budget=tmp_path / "new.json",
        quote=tmp_path / "quote.json",
        authorized_at="2026-09-18T06:44:24+00:00",
    )
    monkeypatch.setattr(
        provider_quota_budget,
        "read_quota_budget",
        lambda path: {"limit_quota": 2_000_000},
    )
    outcomes = [
        result(),
        result("quota", "provider quota budget does not cover the request reservation"),
        result("wall_limit"),
    ]
    calls, allocations = [], []

    def execute(wave):
        calls.append(wave)
        clock[0] += 60
        return outcomes.pop(0)

    scale = {
        "source_hashes": lambda: {"file": "same"},
        "execute": execute,
        "verified_quote_snapshot": lambda path: (b"", {}),
        "create_isolated_operator_budget": lambda *args, **kwargs: allocations.append(
            (args, kwargs)
        ),
    }
    state = supervisor["supervise"](
        options,
        scale,
        now=lambda: clock[0],
        pause=lambda delay: clock.__setitem__(0, clock[0] + delay),
    )
    assert len(calls) == 3
    assert [round(c.max_wall) for c in calls] == [60, 54, 53]
    assert [c.quota_budget for c in calls] == [
        options.budget,
        options.budget,
        options.extension_budget,
    ]
    assert len(allocations) == 1
    assert allocations[0][1]["limit_quota"] == 10_000_000
    assert all(not c.no_infrastructure_fuse and not c.ignore_unknown_holds for c in calls)
    assert state["cooldowns_used"] == 1


def test_supervisor_records_remote_warmup_and_stops_when_unavailable(
    supervisor, tmp_path, monkeypatch
):
    from agent.accounting import provider_quota_budget

    options = SimpleNamespace(
        log_dir=tmp_path,
        hours=1,
        budget=tmp_path / "old.json",
        extension_budget=tmp_path / "new.json",
        quote=tmp_path / "quote.json",
        authorized_at="2026-09-18T06:44:24+00:00",
    )
    monkeypatch.setattr(
        provider_quota_budget,
        "read_quota_budget",
        lambda path: {"limit_quota": 2_000_000},
    )
    calls = []
    scale = {
        "source_hashes": lambda: {"file": "same"},
        "remote_warmup_check": lambda: calls.append(True)
        or {"available": False, "ready_for_checks": False, "error": "down"},
        "execute": lambda wave: pytest.fail("unhealthy remote must block paid work"),
    }
    state = supervisor["supervise"](options, scale, now=lambda: 0.0, pause=lambda _delay: None)
    assert calls == [True]
    assert state["status"] == "remote_warmup_failed"
    assert state["remote_warmup"][0]["available"] is False


def test_quota_does_not_bypass_missing_receipt(supervisor):
    r = result("quota", "provider quota budget does not cover the request reservation")
    r["items"].append({"classification": "infrastructure", "new_attempts": []})
    assert supervisor["continuation"](r, retries=0, extended=False) == "stop"


def test_mixed_fault_rolls_over_only_after_all_workers_reconcile(supervisor):
    r = result()
    r["items"].append(
        result("quota", "provider quota budget does not cover the request reservation")["items"][0]
    )
    assert supervisor["continuation"](r, retries=0, extended=False) == "extend"
    r["items"][0]["new_attempts"] = []
    assert supervisor["continuation"](r, retries=0, extended=False) == "stop"


def test_verified_timeout_does_not_consume_provider_retry_budget(supervisor):
    r = result()
    r["items"][0].update(new_attempts=[], receipt_recovery={"safe_to_retry": True})
    assert supervisor["continuation"](r, retries=3, extended=False) == "continue"


def test_deferred_item_is_excluded_from_later_waves(supervisor, tmp_path, monkeypatch):
    from agent.accounting import provider_quota_budget

    options = SimpleNamespace(
        log_dir=tmp_path,
        hours=1,
        budget=tmp_path / "old.json",
        extension_budget=tmp_path / "new.json",
        quote=tmp_path / "q.json",
        authorized_at="2026-09-18T06:44:24+00:00",
    )
    monkeypatch.setattr(
        provider_quota_budget, "read_quota_budget", lambda p: {"limit_quota": 2000000}
    )
    first = result()
    first["items"][0].update(
        batch_id="hard", new_attempts=[], receipt_recovery={"safe_to_retry": True}
    )
    outcomes = [first, result("no_eligible_unseen_items")]
    calls = []

    def execute(wave):
        calls.append(wave)
        return outcomes.pop(0)

    state = supervisor["supervise"](
        options,
        {"source_hashes": lambda: {}, "execute": execute},
        now=lambda: 0,
        pause=lambda _: pytest.fail("no provider cooldown needed"),
    )
    assert calls[0].excluded_batches == ()
    assert calls[1].excluded_batches == ("hard",)
    assert state["deferred_batches"] == ["hard"]


def test_minimum_quota_rollover_precedes_paid_workers(supervisor, tmp_path, monkeypatch):
    from agent.accounting import provider_quota_budget

    options = SimpleNamespace(
        log_dir=tmp_path,
        hours=1,
        budget=tmp_path / "old.json",
        extension_budget=tmp_path / "new.json",
        quote=tmp_path / "q.json",
        authorized_at="2026-09-18T06:44:24+00:00",
    )
    monkeypatch.setattr(
        provider_quota_budget,
        "read_quota_budget",
        lambda p: {
            "limit_quota": 2000000 if p == options.budget else 10000000,
            "remaining_quota": 97000 if p == options.budget else 10000000,
        },
    )
    calls, allocated = [], []

    def execute(wave):
        calls.append(wave)
        return result("wall_limit")

    scale = {
        "source_hashes": lambda: {},
        "execute": execute,
        "minimum_request_quota": lambda p: 107731,
        "verified_quote_snapshot": lambda p: (b"", {}),
        "create_isolated_operator_budget": lambda *a, **kw: allocated.append(kw),
    }
    state = supervisor["supervise"](options, scale, now=lambda: 0)
    assert len(allocated) == 1
    assert calls[0].quota_budget == options.extension_budget
    assert state["extension_activated"]
