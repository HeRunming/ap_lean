"""Focused tests for independent verifier verdict parsing."""

from __future__ import annotations

import pytest

from leanflow_cli.workflows.verification_review import _verification_review_decision


@pytest.mark.parametrize(
    "response",
    [
        "PASS",
        "-PASS",
        "* PASS",
        "**PASS**",
        "- **PASS**",
        "Decision: PASS",
        "# PASS\n- faithful statement",
    ],
)
def test_verdict_parser_accepts_pass_first_line_forms(response: str):
    assert _verification_review_decision({"response": response}) == "PASS"


@pytest.mark.parametrize(
    "response",
    [
        "BLOCK",
        "-BLOCK",
        "* BLOCK",
        "**BLOCK**",
        "- **BLOCK**",
        "Decision: BLOCK",
        "> BLOCK\n- repair the quantifier",
    ],
)
def test_verdict_parser_accepts_block_first_line_forms(response: str):
    assert _verification_review_decision({"response": response}) == "BLOCK"


@pytest.mark.parametrize(
    "response",
    [
        "The candidate is sound.\nPASS",
        "Findings mention PASS but provide no verdict.",
        "The draft is blocked.\nDecision: PASS",
        "BLOCKED",
        "Some prose\nDecision: BLOCK",
    ],
)
def test_verdict_parser_does_not_infer_from_body_or_blocked_prefix(response: str):
    assert _verification_review_decision({"response": response}) == ""


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"response": '{"decision": "PASS"}\nThe body says BLOCK.'}, "PASS"),
        ({"response": '{"status": "BLOCK"}'}, "BLOCK"),
        ({"response": '{"result": "BLOCKED"}'}, ""),
    ],
)
def test_verdict_parser_prefers_exact_structured_json_fields(payload, expected: str):
    assert _verification_review_decision(payload) == expected
