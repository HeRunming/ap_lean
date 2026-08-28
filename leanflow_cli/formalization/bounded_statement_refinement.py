"""Bounded retrieval-and-refinement lane for source-faithful Lean statements."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
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
from leanflow_cli.lean.lean_parsing import _strip_lean_comments_and_strings
from leanflow_cli.lean.lean_services import lean_search
from leanflow_cli.workflows.verification_providers import (
    AUTOFORMALIZER_VERIFICATION_TASK,
    BLUEPRINT_VERIFICATION_TASK,
    VerificationReviewResult,
    run_model_verification_review,
)


class BoundedStatementRefinementError(RuntimeError):
    """Reject an unsafe or malformed bounded statement action."""


def source_fidelity_preflight(statement: str) -> str:
    """Render deterministic semantic hazards visible before the first draft."""
    source = str(statement or "")
    lower = source.lower()
    risks = [
        "Preserve pointwise versus almost-everywhere hypotheses exactly; do not silently strengthen or weaken the source.",
        "Audit Lean-totalized edge cases (zero denominators, Nat subtraction, empty finite types, log at zero) and add source-domain hypotheses only when the source entails them.",
    ]
    if re.search(r"random variable|probabil|expectation|\bmgf\b|moment generating", lower):
        risks.append(
            "Model genuine random variables: make measurability explicit and ensure every ordinary expectation/MGF is integrable or finite when the source requires a finite real value."
        )
        risks.append(
            "Choose Real/NNReal/ENNReal/EReal deliberately. Do not use extended values, totalized subtraction, or Bochner-integral defaults unless their infinity/undefined cases match the source convention."
        )
    if re.search(
        r"\b(?:fix|correct|repair|tighten|improve)\b.{0,80}\b(?:proof|argument)\b",
        lower,
        re.DOTALL,
    ) or re.search(
        r"\b(?:proof|argument)\b.{0,80}\b(?:flawed|incorrect|wrong|gap)\b",
        lower,
        re.DOTALL,
    ):
        risks.append(
            "This is a meta proof-repair exercise: formalize the actual corrected theorem, construction, and conclusion. An isolated arithmetic/helper lemma is not a faithful substitute."
        )
    subparts = re.findall(r"(?m)^\s*\([a-z0-9]+\)\s+", source)
    if len(subparts) >= 2:
        risks.append(
            f"The source has {len(subparts)} explicit subparts. Cover every subpart with declarations whose shared hypotheses and domains remain consistent."
        )
    return "\n".join(f"- {risk}" for risk in risks)


_SOURCE_REFERENCE_RE = re.compile(
    r"\b(Proposition|Theorem|Lemma|Definition|Corollary|Exercise|Example|Remark|Section)"
    r"\s+\$?(\d+(?:\.\d+)+)\$?",
    flags=re.IGNORECASE,
)


def source_references(statement: str) -> tuple[str, ...]:
    """Return stable printed-book references required by a source exercise."""
    return tuple(
        dict.fromkeys(
            f"{kind.title()} {number}"
            for kind, number in _SOURCE_REFERENCE_RE.findall(str(statement or ""))
        )
    )


def source_reference_context_required(statement: str) -> bool:
    """Reject paid guessing when an exercise delegates its actual statement to a reference."""
    return bool(source_references(statement))


def extract_reference_contexts_from_text(
    book_text: str, references: Sequence[str], *, max_chars: int = 7000
) -> dict[str, str]:
    """Extract bounded declaration-shaped slices from a page-aligned book text."""
    text = str(book_text or "")
    contexts: dict[str, str] = {}
    heading = re.compile(
        r"(?im)^\s*(?:(?:Proposition|Theorem|Lemma|Definition|Corollary|Exercise|Remark|"
        r"Example)\s+)?\d+(?:\.\d+)+\b"
    )
    starts = [match.start() for match in heading.finditer(text)]
    for reference in references:
        match = re.search(rf"(?im)^\s*{re.escape(reference)}\b", text)
        resolved_heading = reference
        if match is None and reference.startswith("Section "):
            section_number = reference.removeprefix("Section ").strip()
            match = re.search(rf"(?im)^\s*{re.escape(section_number)}\s+[^\n]+", text)
        if match is None and not reference.startswith("Exercise "):
            number_match = re.search(r"\d+(?:\.\d+)+", reference)
            if number_match is not None:
                fuzzy = re.search(
                    rf"(?im)^\s*(Proposition|Theorem|Lemma|Definition|Corollary|Remark|Example)\s+"
                    rf"{re.escape(number_match.group(0))}\b",
                    text,
                )
                if fuzzy is not None:
                    match = fuzzy
                    resolved_heading = fuzzy.group(0).strip()
        if match is None:
            continue
        end = next((start for start in starts if start > match.start()), len(text))
        context = text[match.start() : min(end, match.start() + max_chars)].strip()
        if resolved_heading.casefold() != reference.casefold():
            context = (
                f"[REFERENCE KIND MISMATCH: source question cites {reference}; "
                f"book heading is {resolved_heading}]\n{context}"
            )
        contexts[reference] = context
    return contexts


def _source_pdf_text(pdf: Path, *, source_file: Path, timeout_s: int) -> str:
    """Extract one source PDF once across campaign worker processes."""
    try:
        fingerprint = f"{pdf.resolve()}:{pdf.stat().st_size}:{pdf.stat().st_mtime_ns}"
    except OSError:
        return ""
    project_root = next(
        (
            parent
            for parent in source_file.parents
            if (parent / "lakefile.lean").is_file() or (parent / "lean-toolchain").is_file()
        ),
        source_file.parent,
    )
    cache = (
        project_root
        / ".leanflow"
        / "source-reference-cache"
        / f"{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()}.txt"
    )
    try:
        cached = cache.read_text(encoding="utf-8")
    except OSError:
        cached = ""
    if cached:
        return cached

    command: list[str]
    if shutil.which("pdftotext"):
        command = ["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), "-"]
    else:
        system_python = Path("/usr/bin/python3")
        if not system_python.is_file():
            return ""
        command = [
            str(system_python),
            "-c",
            (
                "import fitz,sys; d=fitz.open(sys.argv[1]); "
                "sys.stdout.write('\\f'.join(p.get_text('text') for p in d))"
            ),
            str(pdf),
        ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout_s)),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0 or not completed.stdout:
        return ""
    with contextlib.suppress(OSError):
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_name(f".{cache.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(completed.stdout, encoding="utf-8")
        os.replace(temporary, cache)
    return completed.stdout


def resolve_source_reference_context(
    statement: str, *, source_file: Path, timeout_s: int = 60
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Resolve cited book declarations from the nearest source PDF without a model call."""
    references = source_references(statement)
    if not references:
        return {}, ()
    pdfs: list[Path] = []
    for parent in (source_file.parent, *list(source_file.parents)[:4]):
        pdfs.extend(sorted(parent.glob("*.pdf")))
        if pdfs:
            break
    if not pdfs:
        return {}, references
    book_text = _source_pdf_text(pdfs[0], source_file=source_file, timeout_s=timeout_s)
    contexts = extract_reference_contexts_from_text(book_text, references) if book_text else {}
    unresolved_exercises = [
        reference
        for reference in references
        if reference not in contexts and reference.startswith("Exercise ")
    ]
    if unresolved_exercises:
        try:
            qa_payload = json.loads(source_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            qa_payload = []
        records = (
            qa_payload.get("questions", qa_payload.get("items", []))
            if isinstance(qa_payload, Mapping)
            else qa_payload
        )
        by_label = {
            str(item.get("label", "") or item.get("id", "") or "").strip(): item
            for item in records or []
            if isinstance(item, Mapping)
        }
        for reference in unresolved_exercises:
            label = reference.removeprefix("Exercise ").strip()
            item = by_label.get(label)
            if item is None:
                continue
            contexts[reference] = "\n".join(
                [
                    f"Exercise {label} (resolved from the same QA corpus)",
                    f"Question: {str(item.get('question', '') or '').strip()}",
                    f"Solution/reference answer: {str(item.get('solution', '') or item.get('answer', '') or '[none]').strip()}",
                ]
            )
    missing = tuple(reference for reference in references if reference not in contexts)
    return contexts, missing


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
    # `𝓝` is notation for `nhds`, but it is unavailable unless the relevant
    # scoped notation is open. The underlying declaration is import-stable.
    lean_code = lean_code.replace("𝓝", "nhds")
    if not lean_code or "sorry" not in lean_code:
        raise BoundedStatementRefinementError(
            "draft must contain Lean code with a sorry placeholder"
        )
    sanitized_code = _strip_lean_comments_and_strings(lean_code)
    forbidden = re.search(
        r"\b(?:admit|axiom|set_option\s+maxRecDepth|unsafe)\b", sanitized_code
    )
    if forbidden:
        raise BoundedStatementRefinementError(
            f"draft contains forbidden statement-lane token: {forbidden.group(0)}"
        )
    declaration_starts = list(
        re.finditer(
            r"(?m)^\s*(?:private\s+)?(?:theorem|lemma|def|abbrev|structure|class)\b",
            sanitized_code,
        )
    )
    theorem_bodies: list[str] = []
    for index, declaration in enumerate(declaration_starts):
        if not re.match(r"\s*(?:private\s+)?(?:theorem|lemma)\b", declaration.group()):
            continue
        end = (
            declaration_starts[index + 1].start()
            if index + 1 < len(declaration_starts)
            else len(sanitized_code)
        )
        block = sanitized_code[declaration.start() : end]
        proof_markers = list(re.finditer(r":=\s*by\b", block))
        if not proof_markers:
            theorem_bodies.append("")
            continue
        body = block[proof_markers[-1].start() + 2 :]
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
    fidelity_preflight = source_fidelity_preflight(statement)
    reference_contexts, missing_references = resolve_source_reference_context(
        statement,
        source_file=source_file,
        timeout_s=timeout_s,
    )
    if source_reference_context_required(statement) and missing_references:
        diagnostic = (
            "source packet is incomplete: resolve these cited declarations before paid "
            f"statement generation: {', '.join(missing_references)}"
        )
        outcome = {
            "stage": "statements",
            "success": False,
            "exit_code": 2,
            "reason": diagnostic,
            "target_file": target_relative,
            "proof_obligations": 0,
            "cost_usd": 0.0,
            "cost_source": "none",
            "cost_scope": "deterministic_source_context_preflight",
            "provenance": "agent",
            "iterations": 0,
            "candidate_attempts": 0,
            "failure_stage": "source_context",
            "final_diagnostic": diagnostic,
            "candidate_diagnostics": [{"stage": "source_context", "diagnostic": diagnostic}],
            "source_references": list(source_references(statement)),
            "missing_source_references": list(missing_references),
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "recorded_at": datetime.now(UTC).isoformat(),
        }

        def record_incomplete_source(current: Mapping[str, Any]):
            return record_campaign_outcome(current, batch_id=batch_id, outcome=outcome), None

        update_campaign_file(campaign_path, record_incomplete_source)
        return outcome
    reference_context = "\n\n".join(
        f"### {reference}\n{context}" for reference, context in reference_contexts.items()
    )

    previous_outcome = dict(batch.get("last_outcome", {}) or {})
    previous_failure_stage = str(previous_outcome.get("failure_stage", "") or "")
    prior_feedback_items: dict[str, list[str]] = {}
    for attempt in reversed(list(batch.get("attempts", []) or []) + [previous_outcome]):
        if not isinstance(attempt, Mapping):
            continue
        review_findings = "\n".join(
            str(item).strip()
            for item in attempt.get("review_findings", []) or []
            if str(item).strip()
        )
        diagnostics = list(attempt.get("candidate_diagnostics", []) or [])
        if str(attempt.get("review_decision", "") or "").upper() == "BLOCK" and review_findings:
            diagnostics.append({"stage": "semantic_review", "diagnostic": review_findings})
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
            stage_feedback = prior_feedback_items.setdefault(stage, [])
            if (
                stage
                and diagnostic
                and diagnostic not in stage_feedback
                and len(stage_feedback) < 3
            ):
                stage_feedback.append(diagnostic)
    prior_feedback = {
        stage: "\n\n".join(diagnostics)[:6000]
        for stage, diagnostics in prior_feedback_items.items()
        if stage
    }

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
            f"RESOLVED BOOK REFERENCES\n{reference_context or '[none]'}\n\n"
            f"DETERMINISTIC SOURCE-FIDELITY PREFLIGHT\n{fidelity_preflight}\n\n"
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
                    f"SOURCE\n{statement}\n\nRESOLVED BOOK REFERENCES\n"
                    f"{reference_context or '[none]'}\n\nPRIOR KNOWN SEMANTIC RISKS\n"
                    f"{fidelity_preflight}\n"
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
        "reason": (
            "bounded retrieval/refinement statement passed"
            if success
            else "bounded statement refinement exhausted"
        ),
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
        "review_evidence": (
            str(target.with_name("IndependentReview.md").relative_to(root)) if success else ""
        ),
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
