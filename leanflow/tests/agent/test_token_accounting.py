"""Test per-turn token and cost accounting used by formalization campaigns."""

from agent.accounting.token_accounting import TokenAccounter


def test_reported_cost_is_split_between_session_and_current_turn():
    accounter = TokenAccounter()
    accounter.record_usage(
        prompt_tokens=100,
        completion_tokens=10,
        total_tokens=110,
        reported_cost_usd=0.5,
    )
    accounter.start_turn()
    accounter.record_usage(
        prompt_tokens=20,
        completion_tokens=5,
        total_tokens=25,
        reported_cost_usd=0.125,
    )

    summary = accounter.session_summary("unknown-private-model")

    assert summary["turn"]["total_tokens"] == 25
    assert summary["session"]["total_tokens"] == 135
    assert summary["cost"]["provider_reported_turn_usd"] == 0.125
    assert summary["cost"]["provider_reported_total_usd"] == 0.625
