#!/usr/bin/env python3
"""Regression tests for the two failure modes that broke the previous run."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from runtime.claude_client import ClaudeResponse
from runtime.config import Config
from runtime.orchestrator import AgentOrchestrator
from runtime.state import RunState


class FakeClient:
    """Stands in for ClaudeClient so parsing can be tested without the network."""

    def close(self):
        pass


def make_orchestrator():
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch.config = Config("config/config.yaml")
    orch.client = FakeClient()
    orch.agent_sessions = {}
    return orch


def resp(text, stop_reason="end_turn"):
    return ClaudeResponse(
        content=text, usage={"output_tokens": len(text) // 4},
        model="test", stop_reason=stop_reason, raw={},
    )


def test_empty_body_is_unusable():
    """The exact failure that produced quality_score 0.0 last run."""
    orch = make_orchestrator()
    ok, _, reason = orch._evaluate_response("PipelineBuilder", resp(""))
    assert ok is False, "empty body must not be accepted"
    assert "empty body" in reason
    print(f"✓ empty body rejected → {reason}")


def test_raw_newlines_in_code_string():
    """37k-char pipeline_code with literal newlines: strict=False must recover it."""
    orch = make_orchestrator()
    body = '```json\n{"pipeline_code": "def f():\n    return 1\n", "language": "python"}\n```'
    ok, result, reason = orch._evaluate_response("PipelineBuilder", resp(body))
    assert ok is True, f"should parse leniently, got: {reason}"
    assert "def f()" in result["pipeline_code"]
    assert result.get("parse_note"), "lenient parse should be recorded"
    print(f"✓ raw newlines recovered → {len(result['pipeline_code'])} chars, note set")


def test_missing_required_key_is_unusable():
    """Parseable JSON that omits pipeline_code must not reach Validator."""
    orch = make_orchestrator()
    ok, _, reason = orch._evaluate_response(
        "PipelineBuilder", resp('```json\n{"language": "python"}\n```')
    )
    assert ok is False
    assert "pipeline_code" in reason
    print(f"✓ missing key rejected → {reason}")


def test_last_json_block_wins():
    """A retry that quotes the bad block first must still yield the good one."""
    orch = make_orchestrator()
    body = (
        "Previous attempt was truncated:\n```json\n{\"plan\": \n```\n"
        "Corrected:\n```json\n{\"plan\": {\"dag\": {\"nodes\": []}}}\n```"
    )
    ok, result, reason = orch._evaluate_response("ResearchPlanner", resp(body))
    assert ok is True, f"should pick the last block, got: {reason}"
    assert result["plan"]["dag"] == {"nodes": []}
    print("✓ last json block selected over the quoted broken one")


def test_nested_python_fence_in_generated_code():
    """The failure that killed run 3: the emitted pipeline_code carries a
    ```python example inside a docstring, so a non-greedy ```json fence match
    ends at that inner fence and every candidate comes out truncated."""
    orch = make_orchestrator()
    code = (
        '"""Math QA pipeline.\n\nUsage:\n    ```python\n'
        '    run_pipeline(cfg)\n    ```\n"""\n'
        "def run_pipeline(cfg):\n    return 1\n"
    )
    body = "```json\n" + json.dumps(
        {"pipeline_code": code, "language": "python"}, indent=2
    ) + "\n```"
    ok, result, reason = orch._evaluate_response("PipelineBuilder", resp(body))
    assert ok is True, f"nested fence must not defeat extraction, got: {reason}"
    assert "```python" in result["pipeline_code"], "inner fence must survive intact"
    assert "def run_pipeline" in result["pipeline_code"]
    print("✓ nested ```python fence survived — brace matching recovered the object")


def test_prose_before_json_object():
    """A retry often prefaces the block with prose; brace matching must still
    find the object without being fooled by braces in the prose."""
    orch = make_orchestrator()
    body = (
        "My previous attempt broke at {this point} — here is the corrected output.\n\n"
        '```json\n{"plan": {"dag": {"nodes": [{"id": "validate"}]}}}\n```'
    )
    ok, result, reason = orch._evaluate_response("ResearchPlanner", resp(body))
    assert ok is True, f"should recover the real object, got: {reason}"
    assert result["plan"]["dag"]["nodes"][0]["id"] == "validate"
    print("✓ prose with stray braces did not defeat extraction")


def test_repair_hints_survive_the_budget():
    """The bug that starved the repair loop: hints sat at the JSON tail and
    were the first thing a tail-slice cut. They must now outrank bulk."""
    orch = make_orchestrator()
    hints = [{"check": f"c{i}", "priority": "critical", "issue": "x" * 200}
             for i in range(13)]
    ctx = {
        "run_id": "r1",
        "status": "validated",
        "task": {"kind": "test"},
        "outputs": {"PipelineBuilder": {"pipeline_code": "y" * 500000}},
        "recent_events": [{"e": "z" * 1000} for _ in range(50)],
        "repair_hints": hints,
        "validator_verdict": "fail",
    }
    body = orch._render_context(ctx)
    assert "repair_hints" in body, "hints must never be dropped"
    assert '"c12"' in body, "every hint must survive, not just the first few"
    assert len(body) <= orch.config.max_context_chars + 500
    print(f"✓ 13 hints survived a 500k-char context → {len(body):,} chars")


def test_long_code_is_labelled_not_silently_cut():
    """Validator called a complete 961-line file truncated because it saw 77%
    of it with no marker. An excerpt must say so, and keep the tail."""
    orch = make_orchestrator()
    code = "\n".join(f"line_{i} = {i}" for i in range(8000)) + "\nif __name__ == '__main__':\n    main()\n"
    fitted = orch._fit_code(code)
    assert "EXCERPT" in fitted, "an excerpt must be labelled"
    assert "do NOT report it as truncated" in fitted
    assert "__main__" in fitted, "the tail must survive so the file reads as closed"
    assert "line_0 = 0" in fitted, "the head must survive"
    print(f"✓ {code.count(chr(10)) + 1}-line source excerpted with marker, head+tail kept")


def test_lowest_priority_field_drops_first():
    """When the budget bites, whole named fields go — never a JSON mid-slice."""
    orch = make_orchestrator()
    ctx = {
        "run_id": "r1",
        "status": "drafted",
        "task": {"kind": "test"},
        "outputs": {"X": {"data": "d" * 500000}},
        "recent_events": [{"e": "z" * 500} for _ in range(200)],
        "agent_states": {"X": "completed"},
        "evidence_ids": ["e1"],
    }
    body = orch._render_context(ctx)
    assert "omitted field(s)" in body, "a drop must be disclosed"
    assert '"run_id"' in body and '"task"' in body, "top priorities must stay"
    assert json.loads(body.split("\n\n… [context budget")[0]), "the kept part stays valid JSON"
    print("✓ budget pressure dropped tail fields and disclosed them; JSON stayed valid")


def test_context_strips_raw_response():
    """Upstream raw text must not be re-injected into the next agent's prompt."""
    state = RunState(task={"kind": "test"})
    state.outputs["ResearchPlanner"] = {
        "plan": {"dag": {"nodes": [1, 2]}},
        "raw_response": "x" * 50000,
        "attempts": [{"attempt": 1}],
        "agent": "ResearchPlanner",
    }
    ctx = state.get_context_for_agent("FieldMapper")
    rp = ctx["outputs"]["ResearchPlanner"]
    assert "raw_response" not in rp and "attempts" not in rp
    assert rp["plan"]["dag"]["nodes"] == [1, 2], "parsed payload must survive"
    size = len(json.dumps(ctx, ensure_ascii=False))
    assert size < 5000, f"context still bloated: {size}"
    print(f"✓ context stripped → {size} chars (was >50000)")


def test_max_tokens_truncation_is_unusable():
    """ResearchPlanner's 4096-token truncation last run."""
    orch = make_orchestrator()
    truncated = '```json\n{"plan": {"dag": {"nodes": [{"id": "validate"'
    ok, _, reason = orch._evaluate_response(
        "ResearchPlanner", resp(truncated, stop_reason="max_tokens")
    )
    assert ok is False
    assert "max_tokens" in reason
    print(f"✓ truncated JSON rejected → {reason}")


def main():
    print("=" * 62)
    print("解析回归测试 — 复现上次运行的两个失败模式")
    print("=" * 62)
    tests = [
        test_empty_body_is_unusable,
        test_raw_newlines_in_code_string,
        test_missing_required_key_is_unusable,
        test_last_json_block_wins,
        test_context_strips_raw_response,
        test_max_tokens_truncation_is_unusable,
        test_repair_hints_survive_the_budget,
        test_long_code_is_labelled_not_silently_cut,
        test_lowest_priority_field_drops_first,
        test_nested_python_fence_in_generated_code,
        test_prose_before_json_object,
    ]
    for t in tests:
        t()
    print("=" * 62)
    print(f"✅ {len(tests)}/{len(tests)} 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
