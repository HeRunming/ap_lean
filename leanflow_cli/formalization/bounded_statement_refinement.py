"""Bounded retrieval-and-refinement lane for source-faithful Lean statements."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from leanflow_cli.formalization.campaign_store import read_campaign, update_campaign_file
from leanflow_cli.formalization.corpus_campaign import record_campaign_outcome
from leanflow_cli.lean.lean_services import lean_search
from leanflow_cli.workflows.verification_providers import (
    AUTOFORMALIZER_VERIFICATION_TASK,
    BLUEPRINT_VERIFICATION_TASK,
    VerificationReviewResult,
    run_model_verification_review,
)


class BoundedStatementRefinementError(RuntimeError):
    """Reject an unsafe or malformed bounded statement action."""


@dataclass(frozen=True)
class StatementDraft:
    lean_code: str
    declarations: tuple[str, ...]
    source_qualifiers: str
    scope_changes: str
    proof_notes: str


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.DOTALL)
    candidate = fenced.group(1) if fenced else raw
    if not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        candidate = candidate[start : end + 1] if start >= 0 and end > start else ""
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError) as exc:
        raise BoundedStatementRefinementError("statement generator returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise BoundedStatementRefinementError("statement generator JSON is not an object")
    return payload


def parse_statement_draft(text: str) -> StatementDraft:
    """Parse one strict statement-only generator response."""
    payload = _extract_json_object(text)
    lean_code = str(payload.get("lean_code", "") or "").strip()
    # Lean tokenizes the standalone Greek lambda as the `fun` syntax token, so
    # it cannot be used as a binder name even though it is conventional on
    # paper. Normalize this common autoformalizer output before compilation.
    lean_code = re.sub(r"(?<![\w'])λ(?![\w'])", "coeff", lean_code)
    if not lean_code or "sorry" not in lean_code:
        raise BoundedStatementRefinementError(
            "draft must contain Lean code with a sorry placeholder"
        )
    forbidden = re.search(r"\b(?:admit|axiom|set_option\s+maxRecDepth|unsafe)\b", lean_code)
    if forbidden:
        raise BoundedStatementRefinementError(
            f"draft contains forbidden statement-lane token: {forbidden.group(0)}"
        )
    declaration_starts = list(
        re.finditer(
            r"(?m)^\s*(?:private\s+)?(?:theorem|lemma|def|abbrev|structure|class)\b",
            lean_code,
        )
    )
    theorem_bodies: list[str] = []
    for index, declaration in enumerate(declaration_starts):
        if not re.match(r"\s*(?:private\s+)?(?:theorem|lemma)\b", declaration.group()):
            continue
        end = (
            declaration_starts[index + 1].start()
            if index + 1 < len(declaration_starts)
            else len(lean_code)
        )
        block = lean_code[declaration.start() : end]
        proof_markers = list(re.finditer(r":=\s*by\b", block))
        if not proof_markers:
            theorem_bodies.append("")
            continue
        body = block[proof_markers[-1].start() + 2 :]
        body = re.sub(r"/-(?:.|\n)*?-/|--[^\n]*", "", body)
        theorem_bodies.append(re.sub(r"\s+", " ", body).strip())
    if not theorem_bodies or any(body != "by sorry" for body in theorem_bodies):
        raise BoundedStatementRefinementError(
            "every theorem or lemma body in the statement lane must be exactly `by sorry`"
        )
    declarations = payload.get("declarations", []) or []
    if isinstance(declarations, str):
        declarations = [declarations]
    names = tuple(
        str(value.get("name", "") if isinstance(value, Mapping) else value).strip()
        for value in declarations
        if str(value.get("name", "") if isinstance(value, Mapping) else value).strip()
    )
    if not names:
        names = tuple(
            match.group(1)
            for match in re.finditer(
                r"^\s*(?:theorem|lemma|def|abbrev|structure|class)\s+([A-Za-z0-9_'.]+)",
                lean_code,
                flags=re.MULTILINE,
            )
        )
    if not names:
        raise BoundedStatementRefinementError("draft contains no named Lean declaration")
    return StatementDraft(
        lean_code=lean_code.rstrip() + "\n",
        declarations=names,
        source_qualifiers=str(payload.get("source_qualifiers", "") or "none").strip(),
        scope_changes=str(payload.get("scope_changes", "") or "none").strip(),
        proof_notes=str(payload.get("proof_notes", "") or "source proof only").strip(),
    )


def parse_retrieval_queries(text: str, *, limit: int = 3) -> tuple[str, ...]:
    """Parse concise, deduplicated retrieval-planner queries."""
    fenced = re.search(r"```\s*(.*?)\s*```", str(text or ""), flags=re.DOTALL)
    body = fenced.group(1) if fenced else str(text or "")
    seen: set[str] = set()
    queries: list[str] = []
    for line in body.splitlines():
        query = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
        key = query.casefold()
        if (
            not query
            or key in {"lean", "json", "text", "plaintext"}
            or len(query) > 160
            or key in seen
        ):
            continue
        seen.add(key)
        queries.append(query)
        if len(queries) >= limit:
            break
    return tuple(queries)


def _result_usage(result: VerificationReviewResult) -> dict[str, float | int]:
    return {
        "prompt_tokens": max(0, int(result.prompt_tokens or 0)),
        "completion_tokens": max(0, int(result.completion_tokens or 0)),
        "total_tokens": max(0, int(result.total_tokens or 0)),
        "cost_usd": max(0.0, float(result.cost_usd or 0.0)),
    }


def _source_statement(source_file: Path, *, labels: Sequence[str] = ()) -> tuple[str, str, str]:
    payload = json.loads(source_file.read_text(encoding="utf-8"))
    raw_items = payload.get("questions", []) if isinstance(payload, Mapping) else payload
    if not isinstance(raw_items, list):
        raise BoundedStatementRefinementError("bounded statement lane requires a QA JSON item list")
    requested = {str(label) for label in labels if str(label)}
    selected = [
        item
        for item in raw_items
        if isinstance(item, Mapping)
        and (not requested or str(item.get("label", "") or "") in requested)
    ]
    if len(selected) != 1:
        raise BoundedStatementRefinementError(
            f"bounded statement lane requires exactly one selected QA item, found {len(selected)}"
        )
    item = selected[0]
    label = str(item.get("label", "") or "source-item").strip()
    statement = str(item.get("question", item.get("statement", "")) or "").strip()
    proof = str(item.get("solution", item.get("proof", "")) or "").strip()
    if not statement:
        raise BoundedStatementRefinementError("source item has no statement")
    return label, statement[:16000], proof[:12000]


def _format_retrieval_context(entries: Sequence[Mapping[str, Any]]) -> str:
    chunks: list[str] = []
    for entry in entries[:6]:
        query = str(entry.get("query", "") or "")
        results = entry.get("results", []) or []
        chunks.append(f"Query: {query}")
        for result in results[:2]:
            if isinstance(result, Mapping):
                compact = {
                    key: result[key]
                    for key in ("name", "module", "statement", "file", "line", "preview", "match")
                    if key in result
                }
                chunks.append(json.dumps(compact, ensure_ascii=False)[:2400])
    return "\n".join(chunks)[:12000]


def _render_blueprint(
    *,
    source_file: str,
    target_file: str,
    label: str,
    statement: str,
    proof: str,
    draft: StatementDraft,
) -> str:
    declarations = ", ".join(f"`{name}`" for name in draft.declarations)
    return (
        f"# Formalization Blueprint: {source_file}\n\n"
        f"- Source: `{source_file}`\n"
        f"- Target Lean entry file: `{target_file}`\n"
        "- Status: Lean declarations drafted and file-verified; awaiting independent statement/source verification\n\n"
        "## Source Statement Inventory\n\n"
        f"### {label}\n\n"
        f"- Planned Lean declarations: {declarations}\n"
        f"- Source qualifiers: {draft.source_qualifiers}\n"
        f"- Scope changes: {draft.scope_changes}\n"
        "- Statement verification status: awaiting independent review\n"
        f"- Source proof / prover notes: {draft.proof_notes}\n\n"
        f"Source statement:\n\n{statement}\n\n"
        f"Reference proof (optional hint):\n\n{proof or '[not provided]'}\n"
    )


def derive_bounded_statement_target(
    project_root: str | Path,
    *,
    source_file: str,
    batch_id: str,
    selection_kind: str,
) -> str:
    """Derive the same stable target layout as document formalization."""
    root = Path(project_root).expanduser().resolve()

    def safe_name(value: str, default: str) -> str:
        words = re.findall(r"[A-Za-z0-9]+", value or "")
        name = "".join(word[:1].upper() + word[1:] for word in words) if words else default
        if not re.match(r"^[A-Za-z_]", name):
            name = f"{default}{name}"
        return name[:80] or default

    source = Path(source_file)
    target = root / safe_name(root.name, "Formalization") / safe_name(source.stem, "Document")
    if selection_kind != "document":
        digest = hashlib.sha256(batch_id.encode("utf-8")).hexdigest()[:8].upper()
        target /= f"{safe_name(batch_id, 'Scope')}{digest}"
    return str((target / "Main.lean").relative_to(root))


def refine_campaign_statement_bounded(
    campaign_path: str | Path,
    *,
    project_root: str | Path,
    batch_id: str,
    reserve_usd: float,
    provider: str,
    planner_provider: str = "",
    generator_provider: str = "",
    judge_provider: str = "",
    generator_fallback_provider: str = "",
    planner_model: str = "",
    generator_model: str = "",
    generator_fallback_model: str = "",
    judge_model: str = "",
    lake_executable: str = "lake",
    max_iterations: int = 3,
    candidates_per_iteration: int = 1,
    candidate_workers: int = 4,
    timeout_s: int = 120,
    model_call: Callable[..., VerificationReviewResult] = run_model_verification_review,
    search_call: Callable[..., Any] = lean_search,
) -> dict[str, Any]:
    """Run a MathForm-style bounded statement refinement action and account it."""
    root = Path(project_root).expanduser().resolve()
    campaign_path = Path(campaign_path).expanduser().resolve()
    campaign = read_campaign(campaign_path)
    budget = float(campaign.get("budget_usd", 0.0) or 0.0)
    spent = float(campaign.get("spent_usd", 0.0) or 0.0)
    if reserve_usd <= 0 or spent + reserve_usd > budget:
        raise BoundedStatementRefinementError(
            "campaign budget does not cover bounded statement action"
        )
    batch = next(
        (
            item
            for item in campaign.get("batches", []) or []
            if isinstance(item, Mapping) and str(item.get("id", "")) == batch_id
        ),
        None,
    )
    if not isinstance(batch, Mapping):
        raise BoundedStatementRefinementError(f"unknown campaign batch: {batch_id}")
    if str(batch.get("status", "") or "") in {
        "statements_completed",
        "proofs_completed",
        "completed",
    }:
        raise BoundedStatementRefinementError(
            f"batch {batch_id} already has a completed statement stage"
        )
    source_relative = str(batch.get("source_file", campaign.get("source", "")) or "").strip()
    if not source_relative:
        source_relative = str(campaign.get("source", "") or "").strip()
    target_relative = str(
        dict(batch.get("last_outcome", {}) or {}).get("target_file", "") or ""
    ).strip()
    if not target_relative and source_relative:
        target_relative = derive_bounded_statement_target(
            root,
            source_file=source_relative,
            batch_id=batch_id,
            selection_kind=str(batch.get("selection_kind", "items") or "items"),
        )
    if not source_relative or not target_relative:
        raise BoundedStatementRefinementError("batch is missing source_file or target_file")
    source_file, target = (root / source_relative).resolve(), (root / target_relative).resolve()
    if not source_file.is_relative_to(root) or not target.is_relative_to(root):
        raise BoundedStatementRefinementError("bounded statement path escapes project root")
    label, statement, proof = _source_statement(
        source_file,
        labels=tuple(str(value) for value in batch.get("labels", []) or []),
    )

    previous_outcome = dict(batch.get("last_outcome", {}) or {})
    previous_failure_stage = str(previous_outcome.get("failure_stage", "") or "")
    prior_feedback: dict[str, str] = {}
    for attempt in reversed(list(batch.get("attempts", []) or []) + [previous_outcome]):
        if not isinstance(attempt, Mapping):
            continue
        diagnostics = list(attempt.get("candidate_diagnostics", []) or [])
        diagnostics.append(
            {
                "stage": attempt.get("failure_stage", ""),
                "diagnostic": attempt.get("final_diagnostic", ""),
            }
        )
        for item in reversed(diagnostics):
            if not isinstance(item, Mapping):
                continue
            stage = str(item.get("stage", "") or "")
            diagnostic = str(item.get("diagnostic", "") or "").strip()
            if stage and diagnostic and stage not in prior_feedback:
                prior_feedback[stage] = diagnostic

    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    cost_usd = 0.0
    retrieval_history: list[dict[str, Any]] = []
    last_bad_code = ""
    compile_error = "\n\n".join(
        prior_feedback[stage]
        for stage in ("format_check", "lean_compilation")
        if stage in prior_feedback
    )[:6000]
    semantic_feedback = prior_feedback.get("semantic_review", "")[:6000]
    final_draft: StatementDraft | None = None
    final_review = ""
    iterations = 0
    candidate_attempts = 0
    failure_stage = "not_started"
    candidate_diagnostics: list[dict[str, str]] = []
    candidate_count = max(1, min(8, int(candidates_per_iteration or 1)))
    pool_workers = max(1, min(candidate_count, int(candidate_workers or 1)))
    usage_lock = Lock()

    default_provider = provider or "auto"
    effective_planner_provider = planner_provider or default_provider
    effective_generator_provider = generator_provider or default_provider
    effective_judge_provider = judge_provider or default_provider

    def call_model(
        *,
        task: str,
        prompt: str,
        system_prompt: str,
        model: str,
        max_tokens: int,
        call_provider: str,
    ) -> VerificationReviewResult:
        nonlocal cost_usd
        result = model_call(
            provider=call_provider,
            model=model,
            task=task,
            prompt=prompt,
            system_prompt=system_prompt,
            timeout_s=timeout_s,
            max_tokens=max_tokens,
        )
        measured = _result_usage(result)
        with usage_lock:
            cost_usd += float(measured["cost_usd"])
            for key in usage:
                usage[key] += int(measured[key])
        return result

    for iteration in range(1, max(1, min(3, max_iterations)) + 1):
        iterations = iteration
        planner = call_model(
            task=AUTOFORMALIZER_VERIFICATION_TASK,
            call_provider=effective_planner_provider,
            model=planner_model,
            max_tokens=512,
            system_prompt="You are a Lean 4 retrieval planner. Return only concise search queries.",
            prompt=(
                "Return zero to three essential Lean/Mathlib search queries in one plain code fence. "
                "Do not solve or formalize the theorem. Do not repeat prior queries.\n\n"
                f"STATEMENT\n{statement}\n\nCOMPILER ERROR\n{compile_error or '[none]'}\n\n"
                f"SEMANTIC FEEDBACK\n{semantic_feedback or '[none]'}\n\n"
                f"PRIOR QUERIES\n{[item['query'] for item in retrieval_history]}"
            ),
        )
        if planner.status != "ok":
            failure_stage = "retrieval_planner"
            semantic_feedback = planner.error or "retrieval planner provider failed"
            break
        if cost_usd >= reserve_usd:
            failure_stage = "budget_after_planner"
            semantic_feedback = "reserved cost exhausted after retrieval planning"
            break
        prior = {str(item["query"]).casefold() for item in retrieval_history}
        for query in parse_retrieval_queries(planner.response):
            if query.casefold() in prior:
                continue
            result = search_call(
                query,
                mode="auto",
                cwd=str(root),
                file_path=target_relative,
                limit=2,
            )
            payload = result.to_dict() if hasattr(result, "to_dict") else dict(result or {})
            retrieval_history.append(
                {"query": query, "results": list(payload.get("results", []) or [])[:2]}
            )
        retrieval_context = _format_retrieval_context(retrieval_history)
        generation_prompt = (
            "Produce exactly one JSON object with keys lean_code, declarations (array), "
            "source_qualifiers, scope_changes, proof_notes. lean_code must be a complete Lean file "
            "whose theorem bodies are `by sorry`. Preserve every quantifier and condition. Do not output "
            "proof steps. Never use standalone `λ` as an identifier; use `coeff` instead because Lean "
            "reserves `λ` as lambda syntax. Use the retrieved interfaces only when relevant.\n\n"
            f"SOURCE\n{statement}\n\nREFERENCE PROOF (HINT ONLY)\n{proof}\n\n"
            f"RETRIEVED INTERFACES\n{retrieval_context or '[none]'}\n\n"
            f"PREVIOUS BAD CODE\n{last_bad_code or '[none]'}\n\n"
            f"COMPILER ERROR\n{compile_error or '[none]'}\n\n"
            f"SEMANTIC FEEDBACK\n{semantic_feedback or '[none]'}"
        )

        def generate_candidate() -> VerificationReviewResult:
            return call_model(
                task=AUTOFORMALIZER_VERIFICATION_TASK,
                call_provider=effective_generator_provider,
                model=generator_model,
                max_tokens=5000,
                system_prompt="You translate mathematical statements to Lean 4 signatures only; never prove them.",
                prompt=generation_prompt,
            )

        if candidate_count == 1:
            generated_results = [generate_candidate()]
        else:
            with ThreadPoolExecutor(
                max_workers=pool_workers,
                thread_name_prefix="leanflow-statement-generate",
            ) as pool:
                generated_results = list(
                    pool.map(lambda _index: generate_candidate(), range(candidate_count))
                )
        candidate_attempts += len(generated_results)
        if (
            not any(result.status == "ok" for result in generated_results)
            and generator_fallback_provider
            and generator_fallback_provider != effective_generator_provider
            and cost_usd < reserve_usd
        ):
            generated_results.append(
                call_model(
                    task=AUTOFORMALIZER_VERIFICATION_TASK,
                    call_provider=generator_fallback_provider,
                    model=generator_fallback_model or generator_model,
                    max_tokens=5000,
                    system_prompt="You translate mathematical statements to Lean 4 signatures only; never prove them.",
                    prompt=generation_prompt,
                )
            )
            candidate_attempts += 1
        if not any(result.status == "ok" for result in generated_results):
            failure_stage = "statement_generation"
            semantic_feedback = next(
                (result.error for result in generated_results if result.error),
                "statement generator provider failed",
            )
            break
        if cost_usd >= reserve_usd:
            failure_stage = "budget_after_generation"
            semantic_feedback = "reserved cost exhausted after statement generation"
            break

        drafts: list[StatementDraft] = []
        draft_errors: list[str] = []
        seen_drafts: set[str] = set()
        for generated in generated_results:
            if generated.status != "ok":
                continue
            try:
                draft = parse_statement_draft(generated.response)
            except BoundedStatementRefinementError as exc:
                draft_errors.append(str(exc))
                candidate_diagnostics.append({"stage": "format", "diagnostic": str(exc)[:1000]})
                continue
            digest = hashlib.sha256(draft.lean_code.encode("utf-8")).hexdigest()
            if digest not in seen_drafts:
                seen_drafts.add(digest)
                drafts.append(draft)
        if not drafts:
            failure_stage = "format_check"
            compile_error = "; ".join(dict.fromkeys(draft_errors))[:6000]
            continue

        target.parent.mkdir(parents=True, exist_ok=True)

        def compile_draft(draft: StatementDraft):
            candidate = target.with_name(f"StatementCandidate_{uuid.uuid4().hex}.lean")
            candidate.write_text(draft.lean_code, encoding="utf-8")
            try:
                completed = subprocess.run(
                    [lake_executable, "env", "lean", str(candidate.relative_to(root))],
                    cwd=str(root),
                    check=False,
                    text=True,
                    capture_output=True,
                )
            finally:
                candidate.unlink(missing_ok=True)
            return draft, completed

        with ThreadPoolExecutor(
            max_workers=min(len(drafts), pool_workers),
            thread_name_prefix="leanflow-statement-compile",
        ) as pool:
            compiled = list(pool.map(compile_draft, drafts))
        compile_failures = [item for item in compiled if item[1].returncode != 0]
        compilable_drafts = [draft for draft, result in compiled if result.returncode == 0]
        if not compilable_drafts:
            failure_stage = "lean_compilation"
            last_bad_code = compile_failures[0][0].lean_code[:24000]
            compile_error = "\n\n".join(
                (result.stderr or result.stdout or "Lean compilation failed")[-3000:]
                for _draft, result in compile_failures[:2]
            )[:6000]
            candidate_diagnostics.extend(
                {
                    "stage": "lean_compilation",
                    "diagnostic": (result.stderr or result.stdout or "Lean compilation failed")[
                        -1000:
                    ],
                }
                for _draft, result in compile_failures[:2]
            )
            semantic_feedback = ""
            continue

        review_feedback: list[str] = []
        for draft in compilable_drafts:
            if cost_usd >= reserve_usd:
                failure_stage = "budget_before_semantic_review"
                semantic_feedback = "reserved cost exhausted before semantic review"
                break
            review = call_model(
                task=BLUEPRINT_VERIFICATION_TASK,
                call_provider=effective_judge_provider,
                model=judge_model,
                max_tokens=2500,
                system_prompt="You are an independent source-fidelity judge, not a prover.",
                prompt=(
                    "Start with exactly PASS or BLOCK. PASS only for a bidirectionally faithful Lean statement: "
                    "same objects, domains, quantifier order, hypotheses, conclusion, and edge cases. Ignore sorry. "
                    "Check the actual typeclass semantics of notation such as norms, distances, division, and "
                    "square roots; type-correct notation can still denote the wrong mathematics. Explicitly verify "
                    "that every prior known semantic risk below was remedied, rather than merely changed. Then give "
                    "concise correction feedback.\n\n"
                    f"SOURCE\n{statement}\n\nPRIOR KNOWN SEMANTIC RISKS\n"
                    f"{semantic_feedback or '[none]'}\n\nLEAN\n{draft.lean_code}"
                ),
            )
            final_review = review.response
            if review.status == "ok" and review.response.lstrip().startswith("PASS"):
                final_draft = draft
                break
            review_feedback.append(review.response[:3000] or review.error[:1000])
            candidate_diagnostics.append(
                {
                    "stage": "semantic_review",
                    "diagnostic": (review.response or review.error)[:1000],
                }
            )
        if final_draft is not None:
            break
        last_bad_code = compilable_drafts[0].lean_code[:24000]
        compile_error = ""
        semantic_feedback = "\n\n".join(review_feedback)[:6000]
        failure_stage = "semantic_review"
    success = final_draft is not None
    if success and final_draft is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(final_draft.lean_code, encoding="utf-8")
        blueprint = target.with_name("Blueprint.md")
        blueprint.write_text(
            _render_blueprint(
                source_file=source_relative,
                target_file=target_relative,
                label=label,
                statement=statement,
                proof=proof,
                draft=final_draft,
            )
            .replace(
                "awaiting independent statement/source verification",
                f"approved by {effective_judge_provider} verifier",
            )
            .replace(
                "awaiting independent review",
                f"approved by {effective_judge_provider} verifier",
            ),
            encoding="utf-8",
        )
        target.with_name("IndependentReview.md").write_text(
            "# Independent statement/source review\n\nVerdict: PASS\n\n"
            f"Provider: `{effective_judge_provider}`\n\nReviewer response:\n\n{final_review}\n",
            encoding="utf-8",
        )
    outcome = {
        "stage": "statements",
        "success": success,
        "exit_code": 0 if success else 2,
        "reason": "bounded retrieval/refinement statement passed"
        if success
        else "bounded statement refinement exhausted",
        "target_file": target_relative,
        "proof_obligations": final_draft.lean_code.count("sorry") if final_draft else 0,
        "cost_usd": round(cost_usd, 6),
        "cost_source": "auxiliary_token_usage",
        "cost_scope": "bounded_statement_pipeline",
        "provenance": "agent",
        "iterations": iterations,
        "candidate_attempts": candidate_attempts,
        "candidates_per_iteration": candidate_count,
        "failure_stage": "" if success else failure_stage,
        "final_diagnostic": "" if success else (semantic_feedback or compile_error)[:6000],
        "candidate_diagnostics": candidate_diagnostics[-8:],
        "retry_feedback_source": "+".join(
            stage
            for stage in ("semantic_review", "lean_compilation", "format_check")
            if stage in prior_feedback
        ),
        "retrieval_queries": [item["query"] for item in retrieval_history],
        "statement_providers": {
            "planner": effective_planner_provider,
            "generator": effective_generator_provider,
            "generator_fallback": generator_fallback_provider,
            "judge": effective_judge_provider,
        },
        "review_evidence": str(target.with_name("IndependentReview.md").relative_to(root))
        if success
        else "",
        "review_decision": "PASS" if success else "BLOCK",
        "model": generator_model,
        "provider": provider,
        "usage": usage,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    update_campaign_file(
        campaign_path,
        lambda current: (
            record_campaign_outcome(current, batch_id=batch_id, outcome=outcome),
            None,
        ),
    )
    return outcome
