"""Exercise source contracts across disk, reviewer, ledger and startup boundaries."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from leanflow_cli.formalization import corpus_campaign as ledger
from leanflow_cli.formalization import corpus_campaign_runner as campaign_runner
from leanflow_cli.formalization.bounded_statement_refinement import (
    StatementDraft,
    statement_contract_lint,
)
from leanflow_cli.formalization.statement_contract_gate import document_statement_contract_gate
from leanflow_cli.native import native_runner as runner


@pytest.fixture
def project(monkeypatch, tmp_path):
    """Create actual source metadata and generated modules without invoking Lean."""
    for name in tuple(os.environ):
        if name.startswith("LEANFLOW_FORMALIZATION_"):
            monkeypatch.delenv(name)
    main = tmp_path / "Demo" / "Main.lean"
    main.parent.mkdir()
    main.write_text("import Demo.Sibling\n", encoding="utf-8")
    sibling = main.parent / "Sibling.lean"
    sibling.write_text("def candidate (n : Nat) : Prop := n = 0\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "theorem_blocks": [
                    {
                        "label": "candidate",
                        "kind": "definition",
                        "statement": "Define the predicate that a natural number equals zero.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    blueprint = tmp_path / "Blueprint.md"
    blueprint.write_text("## Source Statement Inventory\n\n### candidate\n", encoding="utf-8")
    for name, value in {
        "LEANFLOW_PROJECT_ROOT": str(tmp_path),
        "LEANFLOW_NATIVE_WORKFLOW_KIND": "formalize",
        "LEANFLOW_FORMALIZATION_STATEMENT_CONTRACT_GATE": "1",
        "LEANFLOW_FORMALIZATION_MANIFEST": str(manifest),
        "LEANFLOW_FORMALIZATION_BLUEPRINT": str(blueprint),
        "LEANFLOW_FORMALIZATION_DOCUMENT_RELATIVE": "Paper.md",
        "LEANFLOW_FORMALIZATION_TARGET_FILE": str(main),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(runner, "_record_agent_activity", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_record_verifier_decision", lambda *a, **k: None)
    return SimpleNamespace(
        root=tmp_path, main=main, sibling=sibling, manifest=manifest, blueprint=blueprint
    )


def _review_spies(monkeypatch):
    """Observe external review and stamp effects without mocking the acceptance gate."""
    provider = Mock(
        return_value={"status": "ok", "provider": "custom", "response": "PASS\nFindings: []"}
    )
    stamp = Mock(return_value=True)
    monkeypatch.setattr(runner, "resolve_verification_provider", lambda _: "custom")
    monkeypatch.setattr(
        runner, "_document_formalization_review_signature", lambda _: "candidate-signature"
    )
    monkeypatch.setattr(
        runner, "_document_formalization_review_prompt", lambda _: "Review the candidate"
    )
    monkeypatch.setattr(runner, "_attach_live_proof_state", lambda prompt, _: prompt)
    monkeypatch.setattr(runner, "_run_advisory_verification_review", provider)
    monkeypatch.setattr(runner, "_stamp_blueprint_statement_review_approved", stamp)
    return provider, stamp


def _review(project, state):
    return runner._run_configured_blueprint_verification(
        None, "", {"active_file": str(project.main)}, state
    )


def test_invalid_sibling_blocks_real_configured_provider_entry_and_approval(project, monkeypatch):
    provider, stamp = _review_spies(monkeypatch)
    project.sibling.write_text("def candidate : Prop := True\n", encoding="utf-8")
    result = _review(project, {})
    assert result["verification_review"]["gate_decision"] == "BLOCK"
    provider.assert_not_called()
    stamp.assert_not_called()
    assert project.main.read_text() == "import Demo.Sibling\n"


def test_repaired_sibling_reaches_provider_and_clears_old_gate_feedback(project, monkeypatch):
    provider, stamp = _review_spies(monkeypatch)
    project.sibling.write_text("def candidate : Prop := True\n", encoding="utf-8")
    state = {}
    _review(project, state)
    provider.assert_not_called()
    assert state["document_formalization_review_feedback_pending"]
    project.sibling.write_text("def candidate (n : Nat) : Prop := n = 0\n", encoding="utf-8")
    _review(project, state)
    provider.assert_called_once()
    stamp.assert_called_once()
    assert not state.get("document_formalization_review_feedback_pending")
    assert not state.get("document_formalization_review_feedback_message")


@pytest.mark.parametrize(
    "source_state", ["missing_manifest", "empty_blocks", "blank_statement", "missing_qa"]
)
def test_nontrivial_candidate_without_authoritative_source_blocks(
    project, monkeypatch, source_state
):
    if source_state in {"missing_manifest", "missing_qa"}:
        monkeypatch.setenv(
            "LEANFLOW_FORMALIZATION_MANIFEST", str(project.root / "missing-manifest.json")
        )
    elif source_state == "empty_blocks":
        project.manifest.write_text('{"theorem_blocks": []}', encoding="utf-8")
    else:
        project.manifest.write_text(
            json.dumps({"theorem_blocks": [{"label": "candidate", "statement": "   "}]}),
            encoding="utf-8",
        )
    if source_state == "missing_qa":
        path = project.root / "campaign.json"
        path.write_text(
            json.dumps({"source": "missing-source.json", "batches": [{"id": "b"}]}),
            encoding="utf-8",
        )
        monkeypatch.setenv("LEANFLOW_FORMALIZATION_CAMPAIGN", str(path))
        monkeypatch.setenv("LEANFLOW_FORMALIZATION_QA_BATCH", "b")
    result = document_statement_contract_gate(str(project.main))
    assert result.get("review_decision") == "BLOCK"
    assert "source" in " ".join(result["issues"]).lower()


def test_empty_intake_scaffold_can_continue_without_source(project, monkeypatch):
    project.sibling.write_text("import Mathlib\n", encoding="utf-8")
    monkeypatch.setenv("LEANFLOW_FORMALIZATION_MANIFEST", "")
    assert document_statement_contract_gate(str(project.main)) == {}


@pytest.mark.parametrize(
    "code,needle",
    [
        ("theorem candidate (P : Prop) (h : P) : P := by exact h\n", "circular"),
        ("theorem candidate (P : Prop) (h : P) : P := h\n", "circular"),
        ("theorem candidate (n : Nat) : n = n := by rfl\n", "self-equality"),
        ("theorem candidate : True := by sorry\n", "tautological"),
        ("theorem candidate : True := True.intro\n", "tautological"),
        ("def candidate (n : Nat)\n    : Prop\n    := True\n", "tautological"),
    ],
)
def test_completed_or_multiline_tautology_blocks_at_disk_gate(project, code, needle):
    project.sibling.write_text(code, encoding="utf-8")
    result = document_statement_contract_gate(str(project.main))
    assert any(needle in issue for issue in result.get("issues", []))


def test_bounded_lane_does_not_exempt_true_theorem_from_shared_lint():
    draft = StatementDraft(
        lean_code="theorem candidate : True := by sorry\n",
        declarations=("candidate",),
        source_qualifiers="all qualifiers preserved",
        scope_changes="none",
        proof_notes="source argument",
    )
    issues = statement_contract_lint("A nontrivial mathematical theorem", draft)
    assert any("tautological" in issue for issue in issues)


def test_adjacent_declarations_and_identity_function_do_not_form_false_circular_target(project):
    project.sibling.write_text(
        "def identity (n : Nat) : Nat := n\ntheorem first (P Q : Prop) (h : P) (f : P -> Q) : Q := f h\ntheorem second (R S : Prop) (h : R) (f : R -> S) : S := by sorry\n",
        encoding="utf-8",
    )
    assert document_statement_contract_gate(str(project.main)) == {}


@pytest.mark.parametrize("missing_time", [False, True])
def test_probability_contract_read_from_blueprint_controls_provider(
    project, monkeypatch, missing_time
):
    project.manifest.write_text(
        json.dumps(
            {
                "theorem_blocks": [
                    {
                        "label": "candidate",
                        "statement": "For Brownian running supremum at time t_0 under a probability measure, define its event.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    contract = {
        key: "specified explicitly"
        for key in (
            "objects",
            "domains",
            "hypotheses",
            "conclusion",
            "measure_space",
            "measurability",
            "integrability",
            "time_domain",
        )
    }
    if missing_time:
        contract.pop("time_domain")
    project.blueprint.write_text(
        "## Source Statement Inventory\n\n### candidate\n- Source fidelity contract: "
        + json.dumps(contract)
        + "\n",
        encoding="utf-8",
    )
    provider, stamp = _review_spies(monkeypatch)
    result = _review(project, {})
    if missing_time:
        provider.assert_not_called()
        stamp.assert_not_called()
        assert "time_domain" in result["verification_review"]["response"]
    else:
        provider.assert_called_once()
        stamp.assert_called_once()


def _new_campaign(project, monkeypatch):
    (project.root / "source.json").write_text(
        json.dumps([{"label": "candidate", "statement": "Define the zero predicate."}]),
        encoding="utf-8",
    )
    path = project.root / "campaign.json"
    path.write_text(
        json.dumps(
            {
                "source": "source.json",
                "budget_usd": 50,
                "batches": [
                    {
                        "id": "b",
                        "status": "statement_escalate",
                        "labels": ["candidate"],
                        "attempts": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    for name, value in {
        "LEANFLOW_FORMALIZATION_CAMPAIGN": str(path),
        "LEANFLOW_FORMALIZATION_QA_BATCH": "b",
        "LEANFLOW_FORMALIZATION_ESCALATED": "1",
        "LEANFLOW_FORMALIZATION_MAX_SEMANTIC_REPAIRS": "3",
        "LEANFLOW_FORMALIZATION_MAX_INFRASTRUCTURE_RETRIES": "5",
    }.items():
        monkeypatch.setenv(name, value)
    return path


def _record_gate_block(project, path):
    """Use actual handoff and native ledger finalization, without mocking gate transport."""
    handoff = runner._document_formalization_handoff_verification(str(project.main))
    assert handoff["statement_contract"]["review_decision"] == "BLOCK"
    runner._record_formalization_campaign_stage(
        2, {"active_file": str(project.main), "document_formalization_handoff": handoff}, {}, {}
    )
    receipt = json.loads(path.read_text())["batches"][0]["last_outcome"]
    assert receipt["review_findings"] == handoff["statement_contract"]["review_findings"]
    return receipt


def _next_generator_prompt(project, path, monkeypatch, *, custom_startup=False):
    """Feed actual campaign Popen environment into native's real startup prompt builder."""
    prompts = []
    for name in (
        "_recover_persisted_checkpoint_advisories",
        "_startup_active_skill_contract",
        "_startup_additional_skill_contracts",
        "_construction_only_handoff_block",
        "_advisor_circuit_handoff_block",
        "_target_knowledge_for_assignment",
        "artifact_context_block",
    ):
        monkeypatch.setattr(runner, name, lambda *a, **k: "")
    monkeypatch.setattr(runner, "_effective_skill_name", lambda _: "formalize")
    monkeypatch.setattr(runner, "_single_queue_item_turn_enabled", lambda: False)
    monkeypatch.setattr(
        runner, "_document_formalization_organization_phase_active", lambda *a: False
    )
    monkeypatch.setattr(runner, "_swarm_enabled", lambda: False)
    monkeypatch.setattr(runner.learnings, "scope_entry_priors_block", lambda: "")
    monkeypatch.setattr(
        runner, "route_workflow_step", lambda *a, **k: SimpleNamespace(to_dict=lambda: {})
    )
    monkeypatch.setattr(campaign_runner, "try_zero_cost_proof_preflight", lambda *a, **k: None)
    monkeypatch.setattr(
        campaign_runner, "campaign_statement_source_admission", lambda *a, **k: (None, ())
    )

    class Process:
        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    def launch(argv, **kwargs):
        child_env = dict(kwargs["env"])
        child_env["LEANFLOW_FORMALIZATION_DOCUMENT_RELATIVE"] = "Paper.md"
        child_env["LEANFLOW_NATIVE_WORKFLOW_KIND"] = "formalize"
        child_env["LEANFLOW_NATIVE_WORKFLOW_COMMAND"] = "workflow formalize source.json"
        child_env["LEANFLOW_NATIVE_STARTUP_PROMPT"] = (
            "Generate one candidate for the requested source." if custom_startup else ""
        )
        with patch.dict(os.environ, child_env, clear=True):
            prompts.append(runner._startup_user_message(live_state={}, autonomy_state={}))
        return Process()

    monkeypatch.setattr(campaign_runner.subprocess, "Popen", launch)
    action = campaign_runner.CampaignAction(
        stage="statements",
        batch_id="b",
        labels=("candidate",),
        argv=("python", "-m", "leanflow_cli.main", "workflow", "formalize", "source.json"),
    )
    campaign_runner._execute_campaign_action_impl(
        action,
        campaign_path=path,
        campaign=json.loads(path.read_text()),
        project_root=project.root,
        reserve_usd=1,
        environ={},
    )
    assert len(prompts) == 1
    return prompts[0]


@pytest.mark.parametrize("infrastructure_between", [False, True])
@pytest.mark.parametrize("custom_startup", [False, True])
def test_gate_handoff_ledger_feedback_reaches_next_generator_prompt(
    project, monkeypatch, infrastructure_between, custom_startup
):
    project.sibling.write_text("def candidate : Prop := True\n", encoding="utf-8")
    provider, stamp = _review_spies(monkeypatch)
    _review(project, {})
    provider.assert_not_called()
    stamp.assert_not_called()
    path = _new_campaign(project, monkeypatch)
    receipt = _record_gate_block(project, path)
    if infrastructure_between:
        runner._record_formalization_campaign_stage(
            2,
            {
                "active_file": str(project.main),
                "document_formalization_handoff": {
                    "statement_contract": document_statement_contract_gate(str(project.main))
                },
            },
            {
                "operational_pause": "paused_infrastructure",
                "infrastructure_pause_reason": "Connection error",
            },
            {},
        )
        batch = json.loads(path.read_text())["batches"][0]
        assert batch["last_outcome"]["retry_class"] == "infrastructure"
        assert sum(attempt.get("retry_class") == "semantic" for attempt in batch["attempts"]) == 1
        assert not batch["last_outcome"].get("semantic_retry_limit_exhausted")
        assert "review_decision" not in batch["last_outcome"]
        assert "review_findings" not in batch["last_outcome"]
    prompt = _next_generator_prompt(project, path, monkeypatch, custom_startup=custom_startup)
    assert receipt["review_findings"][0] in prompt
    assert "deterministic" in prompt.lower()


def test_new_semantic_pass_supersedes_old_block_in_next_generator_prompt(project, monkeypatch):
    path = _new_campaign(project, monkeypatch)
    project.sibling.write_text("def candidate : Prop := True\n", encoding="utf-8")
    receipt = _record_gate_block(project, path)
    updated = ledger.record_campaign_outcome(
        json.loads(path.read_text()),
        batch_id="b",
        outcome={
            "stage": "statements",
            "success": True,
            "review_decision": "PASS",
            "review_provider": "custom",
            "escalated": True,
        },
    )
    updated = ledger.record_campaign_outcome(
        updated,
        batch_id="b",
        outcome={
            "stage": "statements",
            "success": False,
            "retry_class": "infrastructure",
            "escalated": True,
            "reason": "Connection error",
            "max_infrastructure_retries": 5,
        },
    )
    path.write_text(json.dumps(updated), encoding="utf-8")
    monkeypatch.setenv("LEANFLOW_FORMALIZATION_REVIEW_FEEDBACK", receipt["review_findings"][0])
    monkeypatch.setenv(
        "LEANFLOW_FORMALIZATION_REVIEW_FEEDBACK_PROMPT", receipt["review_findings"][0]
    )
    prompt = _next_generator_prompt(project, path, monkeypatch)
    assert receipt["review_findings"][0] not in prompt
