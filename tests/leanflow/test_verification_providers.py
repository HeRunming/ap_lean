"""Tests for bounded model-verification provider dispatch."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.providers.isolated_auxiliary import (
    AuxiliaryTextResponse,
    IsolatedAuxiliaryError,
    IsolatedAuxiliaryTransientGateway,
)
from leanflow_cli.workflows import verification_providers
from tools.utilities.interrupt import CooperativeInterrupt, set_interrupt


def test_model_review_uses_normalized_isolated_result(monkeypatch):
    monkeypatch.setattr(
        verification_providers,
        "run_isolated_auxiliary_text",
        lambda **_kwargs: AuxiliaryTextResponse(
            content='{"route":"probe"}',
            model="gpt-5.6-terra",
            prompt_tokens=1000,
            completion_tokens=100,
            total_tokens=1100,
        ),
    )
    monkeypatch.setattr(
        verification_providers,
        "_record_verification_activity",
        lambda *_args, **_kwargs: None,
    )

    result = verification_providers.run_model_verification_review(
        provider="main",
        task="orchestration",
        prompt="choose a route",
        timeout_s=7,
    )

    assert result.status == "ok"
    assert result.response == '{"route":"probe"}'
    assert result.model == "gpt-5.6-terra"
    assert result.timed_out is False
    assert result.prompt_tokens == 1000
    assert result.completion_tokens == 100
    assert result.total_tokens == 1100
    assert result.cost_usd == pytest.approx(0.013)


def test_review_timeout_is_bounded_and_retry_count_is_configurable(monkeypatch):
    monkeypatch.setenv("LEANFLOW_ADVISORY_VERIFICATION_TIMEOUT_S", "9999")
    assert verification_providers.verification_review_timeout_s() == 3600
    assert verification_providers.verification_review_timeout_s(1200) == 1200
    assert verification_providers.verification_review_timeout_s(9999) == 3600
    monkeypatch.setenv("LEANFLOW_AUXILIARY_RETRY_COUNT", "99")
    assert verification_providers._auxiliary_retry_count() == 3
    monkeypatch.setenv("LEANFLOW_AUXILIARY_RETRY_COUNT", "-4")
    assert verification_providers._auxiliary_retry_count() == 0
    monkeypatch.setenv("LEANFLOW_AUXILIARY_TIMEOUT_RETRY_COUNT", "99")
    assert verification_providers._auxiliary_timeout_retry_count() == 2


def test_review_uses_configured_timeout_when_omitted(monkeypatch):
    observed: list[float] = []
    monkeypatch.setenv("LEANFLOW_ADVISORY_VERIFICATION_TIMEOUT_S", "42")
    monkeypatch.setattr(
        verification_providers,
        "run_isolated_auxiliary_text",
        lambda **kwargs: observed.append(kwargs["timeout"])
        or AuxiliaryTextResponse(content="PASS"),
    )
    monkeypatch.setattr(
        verification_providers,
        "_record_verification_activity",
        lambda *_args, **_kwargs: None,
    )
    verification_providers.run_model_verification_review(
        provider="main", task="orchestration", prompt="review"
    )
    assert observed == [42]


def test_model_review_forwards_explicit_model_without_process_env_mutation(monkeypatch):
    observed = []

    def fake_identity(**kwargs):
        observed.append(("identity", kwargs.get("model")))
        return SimpleNamespace(provider="custom", model=kwargs.get("model"))

    def fake_call(**kwargs):
        observed.append(("call", kwargs.get("model")))
        return AuxiliaryTextResponse(content="PASS", model=kwargs.get("model") or "")

    monkeypatch.setattr(verification_providers, "resolve_auxiliary_call_identity", fake_identity)
    monkeypatch.setattr(verification_providers, "run_isolated_auxiliary_text", fake_call)
    monkeypatch.setattr(
        verification_providers,
        "_record_verification_activity",
        lambda *_args, **_kwargs: None,
    )

    result = verification_providers.run_model_verification_review(
        provider="mathform-remote",
        model="MathForm-8B",
        task="autoformalizer_verification",
        prompt="formalize",
    )

    assert observed == [("identity", "MathForm-8B"), ("call", "MathForm-8B")]
    assert result.model == "MathForm-8B"


def test_model_review_retries_502_with_distinct_payload_and_requested_model(monkeypatch):
    prompts: list[str] = []
    events: list[str] = []

    def fake_call(**kwargs):
        prompts.append(kwargs["messages"][-1]["content"])
        if len(prompts) == 1:
            raise IsolatedAuxiliaryTransientGateway("HTTP 502 Bad Gateway")
        return AuxiliaryTextResponse(content="PASS", model="gpt-5.6-terra")

    monkeypatch.setenv("LEANFLOW_AUXILIARY_RETRY_BACKOFFS", "0")
    monkeypatch.setattr(verification_providers, "run_isolated_auxiliary_text", fake_call)
    monkeypatch.setattr(
        verification_providers,
        "_record_verification_activity",
        lambda event, *_args, **_kwargs: events.append(event),
    )

    result = verification_providers.run_model_verification_review(
        provider="main",
        model="gpt-5.6-sol",
        task="autoformalizer_verification",
        prompt="formalize",
        timeout_s=7,
    )

    assert result.status == "ok"
    assert result.model == "gpt-5.6-sol"
    assert result.transient_gateway_failure is True
    assert len(prompts) == 2
    assert prompts[0] != prompts[1]
    assert "verification-review-transient-gateway" in events


def test_model_review_emits_progress_heartbeat(monkeypatch):
    events: list[tuple[str, dict]] = []

    def fake_call(**kwargs):
        kwargs["progress_callback"](31.0, 180.0)
        return AuxiliaryTextResponse(content="PASS", model="gpt-5.6-sol")

    monkeypatch.setattr(verification_providers, "run_isolated_auxiliary_text", fake_call)
    monkeypatch.setattr(
        verification_providers,
        "resolve_auxiliary_call_identity",
        lambda **_kwargs: SimpleNamespace(provider="openai-codex", model="gpt-5.6-sol"),
    )
    monkeypatch.setattr(
        verification_providers,
        "_record_verification_activity",
        lambda event, _message, **details: events.append((event, details)),
    )

    result = verification_providers.run_model_verification_review(
        provider="codex",
        task="blueprint_verification",
        prompt="review",
        timeout_s=180,
    )

    assert result.status == "ok"
    heartbeat = next(
        details for event, details in events if event == "verification-review-heartbeat"
    )
    assert heartbeat["provider"] == "openai-codex"
    assert heartbeat["elapsed_s"] == 31.0


def test_model_review_does_not_translate_cooperative_interrupt_to_provider_error(monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(
        verification_providers,
        "run_isolated_auxiliary_text",
        lambda **_kwargs: calls.append(True),
    )
    set_interrupt(True)
    try:
        with pytest.raises(CooperativeInterrupt, match="before launch"):
            verification_providers.run_model_verification_review(
                provider="main",
                task="planner_synthesis",
                prompt="choose a plan",
            )
    finally:
        set_interrupt(False)

    assert calls == []


def test_model_review_correlates_request_and_result_telemetry(monkeypatch):
    events: list[tuple[str, dict]] = []
    times = iter((100.0, 102.5))
    monkeypatch.setattr(verification_providers.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        verification_providers,
        "run_isolated_auxiliary_text",
        lambda **_kwargs: AuxiliaryTextResponse(content="PASS", model="control-model"),
    )
    monkeypatch.setattr(
        verification_providers,
        "_record_verification_activity",
        lambda event_type, _message, **details: events.append((event_type, details)),
    )

    verification_providers.run_model_verification_review(
        provider="main",
        task="orchestration",
        prompt="choose a route",
        timeout_s=7,
    )

    assert [event_type for event_type, _details in events] == [
        "verification-review-request",
        "verification-review-result",
    ]
    request = events[0][1]
    result = events[1][1]
    assert request["review_id"] == result["review_id"]
    assert request["review_id"]
    assert request["timeout_s"] == result["timeout_s"] == 7
    assert request["elapsed_s"] == 0.0
    assert result["elapsed_s"] == 2.5


def test_command_review_correlates_request_and_result_telemetry(monkeypatch):
    events: list[tuple[str, dict]] = []
    times = iter((200.0, 201.25))
    monkeypatch.setattr(verification_providers.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        verification_providers,
        "run_command_expert_help",
        lambda **_kwargs: SimpleNamespace(
            provider="codex",
            response="PASS",
            command=["codex", "exec"],
            exit_status=0,
            truncated=False,
            response_chars=4,
            max_response_chars=1000,
            timed_out=False,
        ),
    )
    monkeypatch.setattr(
        verification_providers,
        "_record_verification_activity",
        lambda event_type, _message, **details: events.append((event_type, details)),
    )

    verification_providers.run_command_verification_review(
        provider="codex",
        task="blueprint_verification",
        prompt="review this blueprint",
        timeout_s=11,
    )

    request = events[0][1]
    result = events[1][1]
    assert request["review_id"] == result["review_id"]
    assert request["timeout_s"] == result["timeout_s"] == 11
    assert request["elapsed_s"] == 0.0
    assert result["elapsed_s"] == 1.25


def test_model_review_reports_hard_timeout(monkeypatch):
    monkeypatch.setenv("LEANFLOW_AUXILIARY_TIMEOUT_RETRY_COUNT", "0")

    def timeout(**_kwargs):
        raise verification_providers.IsolatedAuxiliaryTimeout("auxiliary call exceeded 7 seconds")

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(verification_providers, "run_isolated_auxiliary_text", timeout)
    monkeypatch.setattr(
        verification_providers,
        "_record_verification_activity",
        lambda event_type, _message, **details: events.append((event_type, details)),
    )

    result = verification_providers.run_model_verification_review(
        provider="main",
        task="orchestration",
        prompt="choose a route",
        timeout_s=7,
    )

    assert result.status == "timeout"
    assert result.timed_out is True
    assert result.response == ""
    assert "7 seconds" in result.error
    assert events[-1][0] == "verification-review-result"
    assert events[-1][1]["status"] == "timeout"
    assert events[-1][1]["timed_out"] is True
    assert result.failure_class == "reviewer_timeout"


def test_model_review_retries_an_immediate_timeout_with_fresh_request(monkeypatch):
    calls: list[float] = []
    events: list[str] = []

    def timeout_then_pass(**kwargs):
        calls.append(kwargs["timeout"])
        if len(calls) == 1:
            raise verification_providers.IsolatedAuxiliaryTimeout("worker timed out before launch")
        return AuxiliaryTextResponse(content="PASS", model="gpt-6-astra")

    monkeypatch.setenv("LEANFLOW_AUXILIARY_RETRY_BACKOFFS", "0")
    monkeypatch.setattr(verification_providers, "run_isolated_auxiliary_text", timeout_then_pass)
    monkeypatch.setattr(
        verification_providers,
        "_record_verification_activity",
        lambda event, *_args, **_kwargs: events.append(event),
    )

    result = verification_providers.run_model_verification_review(
        provider="custom",
        model="gpt-6-astra",
        task="autoformalizer_verification",
        prompt="review",
        timeout_s=7,
    )

    assert result.status == "ok"
    assert result.retry_attempts == 1
    assert calls[0] == 7.0
    assert 0 < calls[1] <= 7.0
    assert calls[1] >= calls[0] / 2
    assert "verification-review-timeout-retry" in events


def test_unknown_model_pricing_is_explicitly_unavailable(monkeypatch):
    monkeypatch.setattr(
        verification_providers,
        "run_isolated_auxiliary_text",
        lambda **_kwargs: AuxiliaryTextResponse(
            content="PASS", model="gpt-6-astra", prompt_tokens=100, completion_tokens=20
        ),
    )
    monkeypatch.setattr(
        verification_providers, "_record_verification_activity", lambda *_a, **_k: None
    )
    result = verification_providers.run_model_verification_review(
        provider="custom", model="gpt-6-astra", task="blueprint_verification", prompt="review"
    )
    assert result.total_tokens == 120
    assert result.cost_usd == 0.0
    assert result.pricing_known is False
    assert result.cost_source == "cost_unavailable"


def test_model_review_does_not_send_retry_after_backoff_consumes_deadline(monkeypatch):
    calls: list[float] = []

    def timeout(**kwargs):
        calls.append(kwargs["timeout"])
        raise verification_providers.IsolatedAuxiliaryTimeout("worker timeout")

    monkeypatch.setenv("LEANFLOW_AUXILIARY_RETRY_BACKOFFS", "10")
    monkeypatch.setattr(verification_providers, "run_isolated_auxiliary_text", timeout)
    monkeypatch.setattr(
        verification_providers,
        "_record_verification_activity",
        lambda *_args, **_kwargs: None,
    )

    result = verification_providers.run_model_verification_review(
        provider="custom",
        model="gpt-6-astra",
        task="autoformalizer_verification",
        prompt="review",
        timeout_s=5,
    )

    assert result.status == "timeout"
    assert calls == [5.0]
    assert result.retry_attempts == 0


def test_model_review_preserves_unavailable_status(monkeypatch):
    def unavailable(**_kwargs):
        raise verification_providers.IsolatedAuxiliaryUnavailable("provider missing")

    monkeypatch.setattr(verification_providers, "run_isolated_auxiliary_text", unavailable)
    monkeypatch.setattr(
        verification_providers,
        "_record_verification_activity",
        lambda *_args, **_kwargs: None,
    )

    result = verification_providers.run_model_verification_review(
        provider="main",
        task="orchestration",
        prompt="choose a route",
        timeout_s=7,
    )

    assert result.status == "unavailable"
    assert result.timed_out is False
    assert result.error == "provider missing"
    assert result.failure_class == "provider_unavailable"


def test_model_review_redacts_errors_before_activity_telemetry(monkeypatch):
    secret = "sk-telemetryCredential1234567890"

    def failed(**_kwargs):
        raise IsolatedAuxiliaryError(f"Authorization: Bearer {secret}")

    events: list[tuple[str, dict]] = []
    monkeypatch.setenv("LEANFLOW_REDACT_SECRETS", "0")
    monkeypatch.setattr(verification_providers, "run_isolated_auxiliary_text", failed)
    monkeypatch.setattr(
        verification_providers,
        "_record_verification_activity",
        lambda event_type, _message, **details: events.append((event_type, details)),
    )

    result = verification_providers.run_model_verification_review(
        provider="main",
        task="orchestration",
        prompt="choose a route",
        timeout_s=7,
    )

    assert result.status == "error"
    assert result.failure_class == "provider_error"
    assert secret not in result.error
    assert "Bearer [REDACTED]" in result.error
    assert secret not in events[-1][1]["error"]


def test_model_review_records_resolved_identity_on_error(monkeypatch):
    def failed(**_kwargs):
        raise IsolatedAuxiliaryError(
            "Connection error.",
            provider="custom",
            model="zai-org/GLM-5.2",
        )

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(verification_providers, "run_isolated_auxiliary_text", failed)
    monkeypatch.setattr(
        verification_providers,
        "_record_verification_activity",
        lambda event_type, _message, **details: events.append((event_type, details)),
    )

    result = verification_providers.run_model_verification_review(
        provider="auto",
        task="orchestration",
        prompt="choose a route",
        timeout_s=7,
    )

    assert result.status == "error"
    assert result.provider == "custom"
    assert result.model == "zai-org/GLM-5.2"
    assert events[-1][1]["provider"] == "custom"
    assert events[-1][1]["requested_provider"] == "auto"
    assert events[-1][1]["model"] == "zai-org/GLM-5.2"
