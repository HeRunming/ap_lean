"""Tests for the extracted formalization_document_runner helpers + native_runner re-export (Phase 2).

These cover the cleanly-pure ``/formalize`` document-formalization helpers moved out of
native_runner: the blueprint manifest/bullet text-parsers (bullet value extraction, the
"missing"/"unresolved" fidelity predicates, the checklist parser) and the workflow-phase
predicates that key off the live-state snapshot. The re-export-identity test pins every moved name
to the same object on ``native_runner`` so existing callers resolve them without a back-import.
"""

from leanflow_cli.formalization import formalization_document_runner as fdr
from leanflow_cli.lean.lean_module_paths import _lean_decl_names_from_planned_value
from leanflow_cli.native import native_runner


def test_native_runner_reexports_are_identical():
    # Every re-exported name in native_runner must be the SAME object as in
    # formalization_document_runner, so callers (including the entangled formalize helpers still
    # living in native_runner) resolve the extracted helpers without a back-import.
    for name in fdr.__all__:
        assert getattr(native_runner, name) is getattr(fdr, name), name


def test_blueprint_bullet_value_extracts_labeled_field():
    entry = "- Statement: theorem foo : True\n- Source: doc.tex\n"
    assert fdr._blueprint_bullet_value(entry, "Statement") == "theorem foo : True"
    assert fdr._blueprint_bullet_value(entry, "Source") == "doc.tex"
    # A label not present yields the empty string, not an error.
    assert fdr._blueprint_bullet_value(entry, "Missing") == ""


def test_blueprint_value_and_block_missing_treat_placeholders_as_missing():
    # Empty / placeholder values count as missing.
    for placeholder in ("", "  ", "_pending_", "pending", "TODO", "tbd"):
        assert fdr._blueprint_value_missing(placeholder) is True
    assert fdr._blueprint_value_missing("doc.tex") is False
    # A real value is not missing; a trailing placeholder after a colon is.
    assert fdr._blueprint_block_missing("Statement: theorem foo") is False
    assert fdr._blueprint_block_missing("Statement: _pending_") is True


def test_blueprint_plan_allows_only_independent_review_status_to_remain_pending(
    tmp_path, monkeypatch
):
    blueprint = tmp_path / "Blueprint.md"
    blueprint.write_text(
        "# Formalization Blueprint\n\n"
        "## Source Statement Inventory\n\n"
        "### 0.1 — variance\n"
        "- Planned Lean declarations: `variance_identity`\n"
        "- Formal statement review:\n"
        "  - The Lean equality matches the source equality.\n"
        "- Source proof / prover notes: expand the squared norm and integrate.\n"
        "- Statement verification status: _pending_ (independent reviewer has not run).\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LEANFLOW_NATIVE_WORKFLOW_KIND", "formalize")
    monkeypatch.setenv("LEANFLOW_FORMALIZATION_DOCUMENT_RELATIVE", "book/questions.json")
    monkeypatch.setenv("LEANFLOW_FORMALIZATION_BLUEPRINT", str(blueprint))

    assert fdr._document_formalization_needs_blueprint_plan() is False


def test_blueprint_plan_accepts_common_agent_heading_variant(tmp_path, monkeypatch):
    blueprint = tmp_path / "Blueprint.md"
    blueprint.write_text(
        "# Formalization Blueprint\n\n"
        "## Source Inventory\n\n"
        "### Source Entry 0.4 — Balancing vectors\n\n"
        "- Planned Lean declarations: `balancing_vectors_exists`\n\n"
        "#### Formal statement review\n\n"
        "The quantified Lean signature covers the source claim.\n\n"
        "- Source qualifiers: finite vectors and signs.\n"
        "- Lean coverage: all source clauses are represented.\n"
        "- Scope changes: none.\n"
        "- Statement verification status: awaiting independent review.\n\n"
        "#### Source proof / prover notes\n\n"
        "Use independent random signs and expand the squared norm.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LEANFLOW_NATIVE_WORKFLOW_KIND", "formalize")
    monkeypatch.setenv("LEANFLOW_FORMALIZATION_DOCUMENT_RELATIVE", "book/questions.json")
    monkeypatch.setenv("LEANFLOW_FORMALIZATION_BLUEPRINT", str(blueprint))

    assert list(fdr._blueprint_source_inventory_entries(blueprint.read_text())) == ["0.4"]
    assert fdr._document_formalization_needs_blueprint_plan() is False


def test_planned_declaration_parser_ignores_markdown_lean_fence_language():
    planned = """```lean
lemma helper : True

theorem main_result : True
```"""

    assert _lean_decl_names_from_planned_value(planned) == ["helper", "main_result"]


def test_scoped_qa_review_prompt_embeds_artifacts_without_requiring_pdf_tools(
    tmp_path, monkeypatch
):
    context = tmp_path / "context.md"
    blueprint = tmp_path / "Blueprint.md"
    target = tmp_path / "Main.lean"
    context.write_text("bounded question source", encoding="utf-8")
    blueprint.write_text("planned statement", encoding="utf-8")
    target.write_text("theorem demo : True := by sorry", encoding="utf-8")
    values = {
        "LEANFLOW_FORMALIZATION_DOCUMENT_RELATIVE": "book/questions.json",
        "LEANFLOW_FORMALIZATION_DOCUMENT_KIND": "qa_json",
        "LEANFLOW_FORMALIZATION_BLUEPRINT": str(blueprint),
        "LEANFLOW_FORMALIZATION_CONTEXT": str(context),
    }
    monkeypatch.setattr(fdr, "_read_text_env", lambda name, default="": values.get(name, default))

    prompt = fdr._document_formalization_review_prompt(
        {"active_file": str(target), "document_formalization_handoff": {"issues": []}}
    )

    assert "do not require `read_pdf`, shell, Lean, or other tool access" in prompt
    assert "do not execute commands" in prompt
    assert "bounded question source" in prompt
    assert "planned statement" in prompt
    assert "theorem demo : True" in prompt


def test_blueprint_fidelity_field_unresolved_flags_unresolved_markers():
    assert fdr._blueprint_fidelity_field_unresolved("fully covered, verified") is False
    # Any of the unresolved markers (case-insensitive) flips it to True.
    assert fdr._blueprint_fidelity_field_unresolved("statement is UNVERIFIED") is True
    # Explicit scope-change disclosures are resolved decisions, not placeholders.
    assert fdr._blueprint_fidelity_field_unresolved("no source assumption is omitted") is False
    assert (
        fdr._blueprint_fidelity_field_unresolved(
            "partial: the source's topological qualifier is not formalized"
        )
        is False
    )
    assert fdr._blueprint_fidelity_field_unresolved("weakened intentionally") is False
    # An empty/placeholder value is unresolved via the block-missing path.
    assert fdr._blueprint_fidelity_field_unresolved("_pending_") is True


def test_blueprint_checklist_item_checked_tristate():
    text = "- [x] statement verified\n- [ ] proof complete\n"
    assert fdr._blueprint_checklist_item_checked(text, "statement verified") is True
    assert fdr._blueprint_checklist_item_checked(text, "proof complete") is False
    # An item that isn't in the checklist at all returns None (distinct from False).
    assert fdr._blueprint_checklist_item_checked(text, "missing item") is None
