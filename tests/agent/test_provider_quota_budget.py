"""Guard shared quota reservations without network, Lean, or provider calls."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from agent.accounting.provider_quota import ProviderQuotaError
from agent.accounting.provider_quota_budget import (
    BUDGET_PATH_ENV,
    IGNORE_UNKNOWN_HOLDS_ENV,
    QUOTE_PATH_ENV,
    ProviderQuotaBudgetExceeded,
    initialize_quota_budget,
    read_quota_budget,
    reserve_provider_request,
)


@pytest.fixture
def quota_env(tmp_path):
    quote = tmp_path / "quote.json"
    quote.write_text(
        json.dumps(
            {
                "unit": "provider_quota",
                "base_url": "https://api.zcloudapi.com/v1",
                "model": "gpt-6-astra",
                "group": "GPT 蒸馏分组",
                "model_ratio": "0.6849315068493151",
                "group_ratio": "1.6",
                "completion_ratio": "5",
                "cache_read_ratio": "0.1",
                "pricing_version": "observed-version",
                "observed_at_utc": datetime.now(UTC).isoformat(),
            }
        )
    )
    return {
        BUDGET_PATH_ENV: str(tmp_path / "budget.json"),
        QUOTE_PATH_ENV: str(quote),
        "LEANFLOW_AUXILIARY_RETRY_COUNT": "0",
        "LEANFLOW_AUXILIARY_TIMEOUT_RETRY_COUNT": "0",
    }


def reserve(env):
    return reserve_provider_request(
        prompt="hello",
        system_prompt="review",
        max_output_tokens=5,
        base_url="https://api.zcloudapi.com/v1",
        model="gpt-6-astra",
        environ=env,
    )


def test_reservation_uses_provider_output_allowance(quota_env):
    quote_path = quota_env[QUOTE_PATH_ENV]
    payload = json.loads(open(quote_path).read())
    payload["provider_input_allowance_tokens"] = 16384
    payload["provider_output_allowance_tokens"] = 16384
    open(quote_path, "w").write(json.dumps(payload))
    path = quota_env[BUDGET_PATH_ENV]
    initialize_quota_budget(path, limit_quota=120000)
    reservation = reserve(quota_env)
    state = read_quota_budget(path)
    item = state["reservations"][reservation.reservation_id]
    assert item["prompt_token_bound"] == 16384
    assert item["completion_token_bound"] == 16384


def test_success_charges_usage_and_resumes_same_budget(quota_env):
    path = quota_env[BUDGET_PATH_ENV]
    initialize_quota_budget(path, limit_quota=1000)
    reservation = reserve(quota_env)
    assert reservation is not None
    before = read_quota_budget(path)
    assert before["held_quota"] > 0
    settled = reservation.settle(prompt_tokens=49, completion_tokens=5, status="ok")
    assert settled["charged_quota"] == 82
    reservation.settle(prompt_tokens=49, completion_tokens=5, status="ok")
    resumed = initialize_quota_budget(path, limit_quota=1000)
    assert resumed["charges_quota"] == 82
    assert resumed["held_quota"] == 0
    assert resumed["remaining_quota"] == 918
    with pytest.raises(ProviderQuotaError, match="cannot be reset"):
        initialize_quota_budget(path, limit_quota=1001)


@pytest.mark.parametrize("status,prompt,completion", [("timeout", 49, 5), ("ok", 0, 0)])
def test_unknown_calls_keep_entire_hold_and_block_more_work(quota_env, status, prompt, completion):
    path = quota_env[BUDGET_PATH_ENV]
    initialize_quota_budget(path, limit_quota=1000)
    reservation = reserve(quota_env)
    assert reservation is not None
    before = read_quota_budget(path)
    reservation.settle(prompt_tokens=prompt, completion_tokens=completion, status=status)
    after = read_quota_budget(path)
    assert after["held_quota"] == before["held_quota"]
    assert after["charges_quota"] == 0
    assert after["remaining_quota"] == before["remaining_quota"]
    with pytest.raises(ProviderQuotaBudgetExceeded):
        reserve(quota_env)


def test_success_only_mode_excludes_unknown_holds_but_preserves_evidence(quota_env):
    path = quota_env[BUDGET_PATH_ENV]
    initialize_quota_budget(path, limit_quota=1000)
    reservation = reserve(quota_env)
    reservation.settle(prompt_tokens=49, completion_tokens=5, status="timeout")
    conservative = read_quota_budget(path)
    assert conservative["unknown_hold_quota"] == conservative["held_quota"] > 0
    assert conservative["quota_accounting_mode"] == "conservative"
    success_only_env = {**quota_env, IGNORE_UNKNOWN_HOLDS_ENV: "1"}
    admitted = reserve(success_only_env)
    assert admitted is not None
    state = read_quota_budget(path, ignore_unknown_holds=True)
    assert state["unknown_hold_quota"] == conservative["unknown_hold_quota"]
    assert state["effective_held_quota"] == state["held_quota"] - state["unknown_hold_quota"]
    assert state["quota_accounting_mode"] == "success_only"


def test_success_only_parallel_reservations_do_not_oversubscribe(quota_env):
    path = quota_env[BUDGET_PATH_ENV]
    initialize_quota_budget(path, limit_quota=1000)
    first = reserve(quota_env)
    first.settle(prompt_tokens=49, completion_tokens=5, status="timeout")
    success_only = {**quota_env, IGNORE_UNKNOWN_HOLDS_ENV: "1"}

    def attempt(_index):
        try:
            return reserve(success_only)
        except ProviderQuotaBudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(attempt, range(8)))
    state = read_quota_budget(path, ignore_unknown_holds=True)
    assert sum(item is not None for item in reservations) > 0
    assert state["effective_held_quota"] <= state["limit_quota"]
    assert state["unknown_hold_quota"] > 0
    assert state["quota_accounting_mode"] == "success_only"


def test_atomic_parallel_reservations_do_not_oversubscribe(quota_env):
    path = quota_env[BUDGET_PATH_ENV]
    initialize_quota_budget(path, limit_quota=1000)

    def attempt(_index):
        try:
            return reserve(quota_env)
        except ProviderQuotaBudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(attempt, range(8)))
    assert sum(item is not None for item in reservations) == 1
    state = read_quota_budget(path)
    assert state["held_quota"] <= 1000
    assert len(state["reservations"]) == 1


def test_independent_processes_share_the_same_quota_lock(quota_env):
    path = quota_env[BUDGET_PATH_ENV]
    initialize_quota_budget(path, limit_quota=1000)
    code = """
from agent.accounting.provider_quota_budget import reserve_provider_request, ProviderQuotaBudgetExceeded
try:
    reserve_provider_request(prompt='hello',system_prompt='review',max_output_tokens=5,
                             base_url='https://api.zcloudapi.com/v1',model='gpt-6-astra')
    print('reserved')
except ProviderQuotaBudgetExceeded:
    print('rejected')
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code],
            env={**os.environ, **quota_env},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    outputs = []
    for process in processes:
        output, error = process.communicate(timeout=20)
        assert process.returncode == 0, error
        outputs.append(output.strip())
    assert outputs.count("reserved") == 1
    assert outputs.count("rejected") == 3
    assert len(read_quota_budget(path)["reservations"]) == 1


def test_usage_overrun_halts_subsequent_requests(quota_env):
    path = quota_env[BUDGET_PATH_ENV]
    initialize_quota_budget(path, limit_quota=10000)
    reservation = reserve(quota_env)
    assert reservation is not None
    reservation.settle(prompt_tokens=3000, completion_tokens=5, status="ok")
    assert read_quota_budget(path)["halt_reason"]
    with pytest.raises(ProviderQuotaBudgetExceeded, match="exceeded"):
        reserve(quota_env)


def test_retry_or_incomplete_gate_configuration_is_rejected(quota_env):
    with pytest.raises(ProviderQuotaError, match="retries disabled"):
        reserve({**quota_env, "LEANFLOW_AUXILIARY_RETRY_COUNT": "1"})
    with pytest.raises(ProviderQuotaError, match="both budget and quote"):
        reserve({BUDGET_PATH_ENV: quota_env[BUDGET_PATH_ENV]})
    assert reserve({}) is None
