"""Exercise finite HDP scheduling without launching provider or Lean processes."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import itertools
import json
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def scale():
    path = Path(__file__).resolve().parents[3] / "hdp-scale"
    loader = importlib.machinery.SourceFileLoader("hdp_scale_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def campaign(tmp_path, count=6):
    path = tmp_path / "campaign.json"
    source = tmp_path / "questions.json"
    source.write_text(
        json.dumps(
            [
                {
                    "label": str(n),
                    "question": f"For every natural n, n + {n} = {n} + n.",
                }
                for n in range(count)
            ]
        )
    )
    path.write_text(
        json.dumps(
            {
                "source": "questions.json",
                "spent_usd": 0,
                "budget_usd": 100,
                "batches": [
                    {
                        "id": f"i{n}",
                        "labels": [str(n)],
                        "status": "pending",
                        "attempts": [],
                    }
                    for n in range(count)
                ],
            }
        )
    )
    return path


def test_dry_preview_preserves_ledger_dependencies_and_complex_items(tmp_path, scale):
    path = campaign(tmp_path)
    data = json.loads(path.read_text())
    data["batches"][0]["source_complexity_tier"] = "complex"
    data["batches"][1]["dependency_labels"] = ["0"]
    data["batches"][2]["status"] = "statement_escalate"
    data["batches"][3]["lease"] = {
        "worker_id": "other",
        "expires_at": "2999-01-01T00:00:00+00:00",
    }
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    result = scale.preview(path, tmp_path, {"i4"}, 6)
    assert {r["batch_id"] for r in result} == {"i0", "i5"}
    assert path.read_bytes() == before
    assert all(r["source_sha256"] for r in result)
    assert not (tmp_path / ".leanflow").exists()


def test_controller_lock_rejects_second_supervisor(tmp_path, scale):
    with scale.controller_lock(tmp_path):
        with pytest.raises(RuntimeError, match="another"):
            with scale.controller_lock(tmp_path):
                pytest.fail("duplicate controller acquired the lock")


@pytest.mark.parametrize(
    "failure", ["accepted", "infrastructure", "auth", "quota", "rate_limit"]
)
def test_finite_controller_drains_and_never_repeats_an_item(
    tmp_path, monkeypatch, scale, failure
):
    path = campaign(tmp_path, 10)
    options = SimpleNamespace(
        project=tmp_path,
        campaign=path,
        workers=4,
        max_items=6,
        max_wall=180,
        quota_limit=2_000_000,
        quote=tmp_path / "quote.json",
    )
    monkeypatch.setattr(scale, "quota_environment", lambda *args: {})
    monkeypatch.setattr(
        scale, "quota_status", lambda env: {"remaining_quota": 1_000_000}
    )
    created = []

    class FakeProcess:
        def __init__(self, command, **kwargs):
            self.batch_id = command[command.index("--batch-id") + 1]
            self.pid = len(created) + 100
            self.finished = False
            self.log_path = Path(kwargs["stdout"].name)
            created.append(self)
            assert command[command.index("--lean-slots") + 1] == "2"
            assert command[command.index("--statement-candidates") + 1] == "1"
            assert "--batch-item-limit" not in command

        def poll(self):
            if not self.finished:
                data = json.loads(path.read_text())
                batch = next(b for b in data["batches"] if b["id"] == self.batch_id)
                attempt = {
                    "stage": "statements",
                    "success": failure == "accepted",
                    "worker_id": f"worker-{self.pid}",
                    "reason": {
                        "auth": "401 Unauthorized",
                        "quota": "quota exceeded",
                        "rate_limit": "429 too many requests",
                    }.get(failure, ""),
                    "infrastructure_failure": failure == "infrastructure",
                }
                batch["attempts"].append(attempt)
                batch["last_outcome"] = attempt
                batch["status"] = (
                    "statements_completed"
                    if failure == "accepted"
                    else "statement_retry"
                )
                path.write_text(json.dumps(data))
                self.log_path.write_text(
                    json.dumps(
                        {
                            "batch_id": self.batch_id,
                            "stage": "statements",
                            "outcome": attempt,
                        }
                    )
                    + "\n"
                )
                self.finished = True
            return 0 if failure == "accepted" else 2

    clock = iter(range(1000))
    result = scale.execute(
        options, popen=FakeProcess, clock=lambda: next(clock), pause=lambda _: None
    )
    assert len(created) == (6 if failure == "accepted" else 4)
    assert len({p.batch_id for p in created}) == len(created)
    assert all(p.finished for p in created)
    assert result["active"] == {}
    assert (
        result["stop_reason"]
        == {
            "accepted": "item_limit",
            "infrastructure": "consecutive_infrastructure_failures",
            "auth": "auth",
            "quota": "quota",
            "rate_limit": "rate_limit",
        }[failure]
    )
    run_dir = next((tmp_path / ".leanflow/scale-runs").iterdir())
    assert (run_dir / "campaign-before.json").is_file()
    assert (run_dir / "campaign-after.json").is_file()
    assert (run_dir / "events.jsonl").is_file()
    assert all(Path(item["log"]).is_file() for item in result["items"])


def test_old_failure_is_not_reused_as_new_success(scale):
    assert scale.failure_kind([], 0) == "infrastructure"
    assert (
        scale.failure_kind(
            [{"success": False, "failure_class": "semantic_review_block"}], 2
        )
        == "semantic"
    )


def test_sigterm_child_persists_aborted_attempt_before_lease_release(
    tmp_path, monkeypatch, scale
):
    path = campaign(tmp_path, count=1)
    options = SimpleNamespace(
        project=tmp_path,
        campaign=path,
        workers=1,
        max_items=1,
        max_wall=45,
        quota_limit=2_000_000,
        quote=tmp_path / "quote.json",
    )
    monkeypatch.setattr(scale, "quota_environment", lambda *args: {})
    monkeypatch.setattr(
        scale, "quota_status", lambda env: {"remaining_quota": 1_000_000}
    )

    class TerminatedProcess:
        pid = 999

        def poll(self):
            return -signal.SIGTERM

    def launch(*args, **kwargs):
        data = json.loads(path.read_text())
        data["batches"][0]["lease"] = {
            "worker_id": "worker-test",
            "expires_at": "2999-01-01T00:00:00+00:00",
        }
        path.write_text(json.dumps(data))
        return TerminatedProcess()

    result = scale.execute(
        options,
        popen=launch,
        clock=itertools.repeat(0).__next__,
        pause=lambda _seconds: None,
    )
    assert result["items"][0]["classification"] == "infrastructure"
    persisted = json.loads(path.read_text())
    batch = persisted["batches"][0]
    assert "lease" not in batch
    attempt = batch["attempts"][-1]
    assert attempt["failure_class"] == "infrastructure"
    assert attempt["aborted"] is True
    assert attempt["inflight"] is True
    assert attempt["interruption_kind"] == "aborted"
    assert attempt["recovery_receipt"]["kind"] == "campaign_interruption"


def test_quote_required_before_any_paid_child(tmp_path, scale):
    path = campaign(tmp_path)
    options = SimpleNamespace(
        project=tmp_path,
        campaign=path,
        workers=4,
        max_items=6,
        max_wall=180,
        quota_limit=100,
        quote=tmp_path / "missing.json",
    )
    with pytest.raises(ValueError, match="quote"):
        scale.execute(options, popen=lambda *a, **k: pytest.fail("must not launch"))


def test_stop_file_prevents_dispatch(tmp_path, monkeypatch, scale):
    path = campaign(tmp_path)
    options = SimpleNamespace(
        project=tmp_path,
        campaign=path,
        workers=4,
        max_items=6,
        max_wall=180,
        quota_limit=100,
        quote=tmp_path / "unused.json",
    )

    def quota_environment(_options, run_dir):
        (run_dir / "STOP").touch()
        return {}

    monkeypatch.setattr(scale, "quota_environment", quota_environment)
    monkeypatch.setattr(scale, "quota_status", lambda env: {"remaining_quota": 100})
    result = scale.execute(
        options, popen=lambda *a, **k: pytest.fail("STOP must prevent launch")
    )
    assert result["stop_reason"] == "stop_file"
    assert result["items"] == []


def test_receipts_require_new_stage_and_matching_worker(tmp_path, scale):
    log = tmp_path / "item.log"
    log.write_text(
        json.dumps(
            {"stage": "statements", "batch_id": "i0", "outcome": {"worker_id": "owner"}}
        )
    )
    item = {"batch_id": "i0", "log": str(log), "attempts_before": 1}
    batch = {
        "attempts": [
            {"worker_id": "owner", "stage": "statements", "success": True},
            {"worker_id": "other", "stage": "statements", "success": True},
            {"worker_id": "owner", "stage": "proofs", "success": True},
        ]
    }
    assert scale.item_receipts(item, batch) == []


def test_import_halted_quota_budget_preserves_charges_and_records_ack(tmp_path, scale):
    source = tmp_path / "old-quota.json"
    destination = tmp_path / "scale-budget.json"
    source.write_text(
        json.dumps(
            {
                "version": 1,
                "unit": "provider_quota",
                "limit_quota": 2_000_000,
                "charges_quota": 147_059,
                "reservations": {},
                "halt_reason": "reported usage exceeded conservative reservation",
            }
        )
    )
    imported = scale.import_halted_quota_budget(source, destination)
    assert imported["charges_quota"] == 147_059
    assert imported["remaining_quota"] == 1_852_941
    assert imported["imported_from_sha256"]
    assert imported["import_acknowledged_at"]
    assert "halt_reason" not in imported
    assert "halt_reason" in json.loads(source.read_text())
    with pytest.raises(RuntimeError, match="already exists"):
        scale.import_halted_quota_budget(source, destination)


def test_quote_validation_requires_current_input_and_output_allowances(scale):
    current = (
        scale.WORKSPACE
        / ".leanflow-home/logs/zcloud-pricing-20260909T091106Z/quota-quote.json"
    )
    contents, metadata = scale.verified_quote_snapshot(current)
    assert len(contents) > 100
    assert metadata["provider_input_allowance_tokens"] >= 16_384
    assert metadata["provider_output_allowance_tokens"] >= 16_384


def test_quote_validation_rejects_outdated_run_snapshot(tmp_path, scale):
    old = (
        scale.WORKSPACE
        / "fate-x-work/.leanflow/scale-runs/20260909T132940Z/quota-quote.json"
    )
    with pytest.raises(ValueError, match="provider_output_allowance_tokens"):
        scale.verified_quote_snapshot(old)


def _isolated_source(path):
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "unit": "provider_quota",
                "limit_quota": 2_000_000,
                "charges_quota": 438_498,
                "reservations": {
                    "charged-1": {"status": "charged", "reserved_quota": 10},
                    "unknown-1": {"status": "unknown", "reserved_quota": 66_114},
                },
                "halt_reason": "reported usage exceeded conservative reservation",
            }
        )
    )


def test_create_isolated_operator_budget_starts_clean_and_links_wave3(tmp_path, scale):
    source = tmp_path / "wave3.json"
    destination = tmp_path / "wave4.json"
    _isolated_source(source)
    before = source.read_bytes()
    quote_metadata = {
        "sha256": "quote-sha",
        "base_url": "https://api.zcloudapi.com/v1",
        "model": "gpt-6-astra",
        "pricing_version": "pricing-sha",
        "provider_input_allowance_tokens": 16_384,
        "provider_output_allowance_tokens": 16_384,
    }
    created = scale.create_isolated_operator_budget(
        source,
        destination,
        limit_quota=3_000_000,
        authorization_ref="wave4-approval-2026-09-10",
        authorized_at="2026-09-10T08:00:00+00:00",
        quote_metadata=quote_metadata,
    )
    assert source.read_bytes() == before
    assert created["charges_quota"] == 0
    assert created["held_quota"] == 0
    assert created["remaining_quota"] == 3_000_000
    assert created["reservations"] == {}
    assert created["operator_authorization"]["reference"] == "wave4-approval-2026-09-10"
    assert (
        created["operator_authorization"]["authorized_at_utc"]
        == "2026-09-10T08:00:00+00:00"
    )
    assert created["operator_authorization"]["raw_quota_limit_source"] == (
        "operator_authorized_new_provider_quota"
    )
    linkage = created["isolated_from"]
    assert linkage["wave"] == "wave3"
    assert linkage["source_sha256"] == scale.digest(before)
    assert linkage["source_summary"]["charges_quota"] == 438_498
    assert linkage["source_summary"]["held_quota"] == 66_114
    assert linkage["source_summary"]["unknown_hold_quota"] == 66_114
    assert linkage["source_summary"]["reservation_status_counts"] == {
        "charged": 1,
        "unknown": 1,
    }
    assert created["provider_quota_evidence"] == quote_metadata


def test_create_isolated_operator_budget_rejects_collision_and_same_path(
    tmp_path, scale
):
    source = tmp_path / "wave3.json"
    destination = tmp_path / "wave4.json"
    _isolated_source(source)
    kwargs = {
        "limit_quota": 100,
        "authorization_ref": "approval",
        "authorized_at": "2026-09-10T08:00:00+00:00",
        "quote_metadata": {"sha256": "quote-sha"},
    }
    with pytest.raises(RuntimeError, match="must not replace"):
        scale.create_isolated_operator_budget(source, source, **kwargs)
    destination.write_text("{}")
    with pytest.raises(RuntimeError, match="already exists"):
        scale.create_isolated_operator_budget(source, destination, **kwargs)


@pytest.mark.parametrize(
    "authorized_at, message",
    [
        ("2026-09-10T08:00:00", "include a timezone"),
        ("not-a-time", "ISO-8601"),
        ("2999-01-01T00:00:00+00:00", "future"),
    ],
)
def test_create_isolated_operator_budget_rejects_invalid_authorization_time(
    tmp_path, scale, authorized_at, message
):
    source = tmp_path / "wave3.json"
    _isolated_source(source)
    with pytest.raises(ValueError, match=message):
        scale.create_isolated_operator_budget(
            source,
            tmp_path / "wave4.json",
            limit_quota=100,
            authorization_ref="approval",
            authorized_at=authorized_at,
            quote_metadata={},
        )
