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
    ]
    for t in tests:
        t()
    print("=" * 62)
    print(f"✅ {len(tests)}/{len(tests)} 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
