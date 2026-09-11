"""Check explicit provider quotas against actual zcloud charge receipts."""

from datetime import UTC, datetime, timedelta

import pytest

from agent.accounting.provider_quota import (
    ProviderQuotaError,
    ProviderQuotaQuote,
    admit_quota_reservation,
    load_provider_quota_quote,
)


@pytest.fixture
def quote():
    return ProviderQuotaQuote.from_mapping(
        {
            "unit": "provider_quota",
            "base_url": "https://api.zcloudapi.com/v1",
            "model": "gpt-6-astra",
            "group": "GPT 蒸馏分组",
            "model_ratio": "0.6849315068493151",
            "group_ratio": "1.6",
            "completion_ratio": "5",
            "cache_read_ratio": "0.1",
            "pricing_version": "a42d372ccf0b5dd13ecf71203521f9d2",
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "evidence_path": "token_logs_canonical.json",
        }
    )


def estimate(quote, **kwargs):
    return quote.estimate_quota(
        base_url=quote.base_url, model=quote.model, group=quote.group, **kwargs
    )


def test_smoke_receipt_has_81_actual_quota_and_82_conservative_reservation(quote):
    assert estimate(quote, prompt_tokens=49, completion_tokens=5, conservative=False) == 81
    assert estimate(quote, prompt_tokens=49, completion_tokens=5) == 82


def test_quote_carries_empirical_provider_input_allowance():
    quote = ProviderQuotaQuote.from_mapping(
        {
            "unit": "provider_quota",
            "base_url": "https://api.zcloudapi.com/v1",
            "model": "gpt-6-astra",
            "group": "GPT 蒸馏分组",
            "model_ratio": "0.6849315068493151",
            "group_ratio": "1.6",
            "completion_ratio": "5",
            "cache_read_ratio": "0.1",
            "pricing_version": "observed",
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "provider_input_allowance_tokens": 16384,
        }
    )
    assert quote.provider_input_allowance_tokens == 16384


def test_quote_carries_empirical_provider_output_allowance():
    payload = {
        "unit": "provider_quota",
        "base_url": "https://api.zcloudapi.com/v1",
        "model": "gpt-6-astra",
        "group": "GPT 蒸馏分组",
        "model_ratio": "0.6849315068493151",
        "group_ratio": "1.6",
        "completion_ratio": "5",
        "cache_read_ratio": "0.1",
        "pricing_version": "observed",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "provider_output_allowance_tokens": 16384,
    }
    assert ProviderQuotaQuote.from_mapping(payload).provider_output_allowance_tokens == 16384


def test_cached_receipt_matches_provider_and_reservation_ignores_discount(quote):
    assert (
        estimate(
            quote,
            prompt_tokens=158046,
            completion_tokens=535,
            cached_input_tokens=157824,
            conservative=False,
        )
        == 20471
    )
    assert estimate(
        quote, prompt_tokens=158046, completion_tokens=535, cached_input_tokens=157824
    ) == estimate(quote, prompt_tokens=158046, completion_tokens=535)


@pytest.mark.parametrize(
    "override", [{"base_url": "https://other.example/v1"}, {"model": "gpt-6"}, {"group": "auto"}]
)
def test_quote_cannot_cross_provider_model_or_route(quote, override):
    with pytest.raises(ProviderQuotaError, match="does not match"):
        quote.estimate_quota(
            **({"base_url": quote.base_url, "model": quote.model, "group": quote.group} | override),
            prompt_tokens=1,
            completion_tokens=1,
        )


@pytest.mark.parametrize("bad", [-1, 1.5, True])
def test_malformed_counts_are_rejected(quote, bad):
    with pytest.raises(ProviderQuotaError, match="nonnegative integer"):
        estimate(quote, prompt_tokens=bad, completion_tokens=1)


def test_cache_cannot_exceed_prompt(quote):
    with pytest.raises(ProviderQuotaError, match="cannot exceed"):
        estimate(quote, prompt_tokens=10, completion_tokens=1, cached_input_tokens=11)


def test_unknown_receipt_hold_cannot_be_reused():
    admit_quota_reservation(
        limit_quota=200, charged_quota=81, outstanding_quota=82, requested_quota=37
    )
    with pytest.raises(ProviderQuotaError, match="does not cover"):
        admit_quota_reservation(
            limit_quota=200, charged_quota=81, outstanding_quota=82, requested_quota=38
        )


def test_invalid_quote_unit_is_not_interpreted_as_dollars():
    with pytest.raises(ProviderQuotaError, match="provider_quota"):
        ProviderQuotaQuote.from_mapping({"unit": "USD"})


@pytest.mark.parametrize("age_hours", [25, -1])
def test_quote_rejects_expired_or_future_observation(age_hours):
    with pytest.raises(ProviderQuotaError, match="expired or in the future"):
        ProviderQuotaQuote.from_mapping(
            {
                "unit": "provider_quota",
                "base_url": "https://api.zcloudapi.com/v1",
                "model": "gpt-6-astra",
                "group": "GPT 蒸馏分组",
                "pricing_version": "version",
                "observed_at_utc": (datetime.now(UTC) - timedelta(hours=age_hours)).isoformat(),
            }
        )


def test_missing_or_corrupt_quote_raises_domain_error(tmp_path):
    path = tmp_path / "quote.json"
    with pytest.raises(ProviderQuotaError, match="missing or invalid"):
        load_provider_quota_quote(path)
    path.write_text("{")
    with pytest.raises(ProviderQuotaError, match="missing or invalid"):
        load_provider_quota_quote(path)
