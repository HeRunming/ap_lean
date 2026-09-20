"""Bounded retrieval-and-refinement lane for source-faithful Lean statements."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from agent.accounting.provider_quota import ProviderQuotaError
from agent.accounting.provider_quota_budget import reserve_provider_request
from core.project_lean_capacity import (
    PROJECT_LEAN_CAPACITY_ENV,
    acquire_project_lean_capacity,
)
from leanflow_cli.formalization.campaign_store import (
    read_campaign,
    update_campaign_file,
)
from leanflow_cli.formalization.corpus_campaign import (
    RETRY_CLASS_INFRASTRUCTURE,
    classify_campaign_retry_class,
    record_campaign_outcome,
)
from leanflow_cli.formalization.declaration_contract_lint import (
    declaration_contract_issues,
)
from leanflow_cli.formalization.project_reachability import project_target_reachability
from leanflow_cli.formalization.remote_warm_probe import (
    RemoteWarmProbe,
    warm_probe_enabled,
)
from leanflow_cli.formalization.statement_compilation_evidence import (
    candidate_signature_probe,
    compilation_review_context,
)
from leanflow_cli.formalization.statement_review_feedback import (
    latest_statement_verdict,
    statement_review_feedback,
)
from leanflow_cli.lean.lean_parsing import _strip_lean_comments_and_strings
from leanflow_cli.lean.lean_services import lean_search
from leanflow_cli.runtime.toolchain_env import add_lean_toolchain_env, discover_lean_bin
from leanflow_cli.workflows.verification_providers import (
    AUTOFORMALIZER_VERIFICATION_TASK,
    BLUEPRINT_VERIFICATION_TASK,
    VerificationReviewResult,
    resolve_model_verification_provider,
    run_model_verification_review,
)
from leanflow_cli.workflows.verification_review import _verification_review_decision


class BoundedStatementRefinementError(RuntimeError):
    """Reject an unsafe or malformed bounded statement action."""


DEFAULT_BOUNDED_STATEMENT_MODEL = "gpt-5.6-sol"
DEFAULT_CANDIDATE_COMPILE_TIMEOUT_S = 120.0
DEFAULT_RETRIEVAL_PLANNER_TIMEOUT_S = 90.0
DEFAULT_RETRIEVAL_PLANNER_MAX_TOKENS = 256
RETRIEVAL_PLANNER_TIMEOUT_ENV = "LEANFLOW_RETRIEVAL_PLANNER_TIMEOUT_S"
MAX_CANDIDATE_COMPILE_TIMEOUT_S = 3600.0
REMOTE_LEAN_TIMEOUT_ENV = "LEANFLOW_REMOTE_LEAN_TIMEOUT_S"
_ORIGINAL_SUBPROCESS_RUN = subprocess.run


@dataclass(frozen=True)
class CandidateCompileResult:
    """Capture one candidate's Lean result, including a bounded timeout."""

    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    timeout_s: float = 0.0
    remote_compile_receipt: Mapping[str, Any] | None = None


def _remote_compile_receipt(stdout: str = "", stderr: str = "") -> Mapping[str, Any] | None:
    """Extract the wrapper's JSON receipt marker without retaining compiler noise."""
    marker = "LEANFLOW_REMOTE_COMPILE_RECEIPT_JSON="
    for line in reversed((stderr or "").splitlines() + (stdout or "").splitlines()):
        if not line.startswith(marker):
            continue
        try:
            value = json.loads(line[len(marker) :])
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, Mapping) else None
    return None


def _persist_statement_review_evidence(
    root: Path,
    *,
    batch_id: str,
    source_relative: str,
    target_relative: str,
    statement: str,
    proof: str,
    draft: StatementDraft,
    review_prompt: str,
) -> tuple[Path, str, str]:
    """Persist one compiled candidate so reviewer recovery never regenerates it."""
    source_digest = hashlib.sha256(f"{statement}\n{proof}".encode()).hexdigest()
    candidate_digest = hashlib.sha256(draft.lean_code.encode()).hexdigest()
    evidence_dir = root / ".leanflow" / "statement-review-evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / f"{batch_id}-{candidate_digest[:16]}.json"
    candidate_path = evidence_dir / f"{batch_id}-{candidate_digest[:16]}.lean"
    blueprint_path = evidence_dir / f"{batch_id}-{candidate_digest[:16]}-Blueprint.md"
    blueprint_text = (
        f"# Statement blueprint\n\nSource: `{source_relative}`\n\n"
        f"Target: `{target_relative}`\n\nStatement:\n\n{statement}\n\n"
        f"Lean candidate:\n\n```lean\n{draft.lean_code}\n```\n"
    )
    if candidate_path.is_file() and (
        hashlib.sha256(candidate_path.read_bytes()).hexdigest() != candidate_digest
    ):
        raise BoundedStatementRefinementError("review evidence candidate file digest mismatch")
    candidate_path.write_text(draft.lean_code, encoding="utf-8")
    blueprint_path.write_text(blueprint_text, encoding="utf-8")
    payload = {
        "batch_id": batch_id,
        "source_relative": source_relative,
        "target_relative": target_relative,
        "source": statement,
        "proof": proof,
        "source_digest": source_digest,
        "candidate_digest": candidate_digest,
        "lean_code": draft.lean_code,
        "review_prompt": review_prompt,
        "candidate_path": str(candidate_path.relative_to(root)),
        "blueprint_path": str(blueprint_path.relative_to(root)),
        "manifest": {
            "source_digest": source_digest,
            "candidate_digest": candidate_digest,
            "blueprint_digest": hashlib.sha256(blueprint_text.encode("utf-8")).hexdigest(),
        },
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if evidence_path.is_file():
        existing = json.loads(evidence_path.read_text(encoding="utf-8"))
        if (
            existing.get("source_digest") != source_digest
            or existing.get("candidate_digest") != candidate_digest
        ):
            raise BoundedStatementRefinementError(
                f"review evidence digest collision: {evidence_path.name}"
            )
    else:
        evidence_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return evidence_path, source_digest, candidate_digest


def review_statement_evidence(
    evidence_path: str | Path,
    *,
    provider: str,
    model: str,
    timeout_s: int = 600,
    max_tokens: int = 2500,
) -> dict[str, Any]:
    """Re-run only the reviewer for a persisted candidate, never the generator."""
    path = Path(evidence_path).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    source = str(payload.get("source", "") or "")
    proof = str(payload.get("proof", "") or "")
    lean_code = str(payload.get("lean_code", "") or "")
    source_digest = hashlib.sha256(f"{source}\n{proof}".encode()).hexdigest()
    candidate_digest = hashlib.sha256(lean_code.encode()).hexdigest()
    if source_digest != str(payload.get("source_digest", "")):
        raise BoundedStatementRefinementError("review evidence source digest mismatch")
    if candidate_digest != str(payload.get("candidate_digest", "")):
        raise BoundedStatementRefinementError("review evidence candidate digest mismatch")
    manifest = payload.get("manifest") if isinstance(payload.get("manifest"), Mapping) else {}
    if manifest.get("source_digest") not in {None, source_digest}:
        raise BoundedStatementRefinementError("review evidence manifest source digest mismatch")
    candidate_path_value = str(payload.get("candidate_path", "") or "")
    if candidate_path_value:
        candidate_path = (path.parent.parent.parent / candidate_path_value).resolve()
        if candidate_path.is_file() and (
            hashlib.sha256(candidate_path.read_bytes()).hexdigest() != candidate_digest
        ):
            raise BoundedStatementRefinementError("review evidence candidate digest mismatch")
    review = run_model_verification_review(
        provider=provider,
        model=model,
        task=BLUEPRINT_VERIFICATION_TASK,
        prompt=str(payload.get("review_prompt", "") or ""),
        system_prompt="You are an independent source-fidelity judge, not a prover.",
        timeout_s=timeout_s,
        max_tokens=max_tokens,
    )
    decision = _verification_review_decision({"response": review.response})
    return {
        "status": review.status,
        "review_decision": decision,
        "semantic_retry_allowed": decision == "BLOCK",
        "response": review.response,
        "error": review.error,
        "failure_class": review.failure_class,
        "source_digest": source_digest,
        "candidate_digest": candidate_digest,
        "evidence_path": str(path),
    }


def _resolve_candidate_compile_timeout(
    configured: float | int | None, *, fallback: float | int
) -> float:
    """Return a positive candidate timeout clamped to the hard safety ceiling."""
    raw = configured
    if raw is None:
        raw = os.environ.get("LEANFLOW_STATEMENT_COMPILE_TIMEOUT_S", fallback)
    try:
        requested = float(raw)
    except (TypeError, ValueError):
        requested = float(fallback)
    return min(MAX_CANDIDATE_COMPILE_TIMEOUT_S, max(1.0, requested))


def _resolve_retrieval_planner_timeout(
    configured: float | int | None, *, total_timeout_s: float | int
) -> float:
    """Return a short planner deadline bounded by the action deadline.

    The zcloud gateway has a roughly 120 second proxy deadline.  Keeping the
    planner below that boundary lets the statement generator proceed with an
    empty retrieval context when the optional search hint is unavailable.
    """
    raw = configured
    if raw is None:
        raw = os.environ.get(RETRIEVAL_PLANNER_TIMEOUT_ENV, DEFAULT_RETRIEVAL_PLANNER_TIMEOUT_S)
    try:
        requested = float(raw)
    except (TypeError, ValueError):
        requested = DEFAULT_RETRIEVAL_PLANNER_TIMEOUT_S
    try:
        total = float(total_timeout_s)
    except (TypeError, ValueError):
        total = DEFAULT_RETRIEVAL_PLANNER_TIMEOUT_S
    return max(1.0, min(DEFAULT_RETRIEVAL_PLANNER_TIMEOUT_S, requested, total))


def _planner_failure_is_transient(result: VerificationReviewResult) -> bool:
    """Identify optional planner transport failures safe for empty-context fallback."""
    failure_class = str(getattr(result, "failure_class", "") or "").casefold()
    error = str(getattr(result, "error", "") or "")
    lowered = error.casefold()
    # Quota, authentication, and provider-resolution failures must stop the
    # action.  A planner fallback is only for a request that reached the
    # provider but could not complete due to a transient transport deadline.
    hard_markers = (
        "quota",
        "budget",
        "authentication",
        "unauthorized",
        "forbidden",
        "api key",
        "no llm provider",
        "provider unavailable",
    )
    if failure_class in {"budget_limit", "provider_unavailable"} or any(
        marker in lowered for marker in hard_markers
    ):
        return False
    transient_markers = (
        "timeout",
        "timed out",
        "connection reset",
        "connection refused",
        "connection error",
        "connecterror",
        "read error",
        "remote disconnected",
        "temporarily unavailable",
        "gateway",
        " 502",
        " 504",
        " 524",
    )
    return bool(getattr(result, "timed_out", False)) or any(
        marker in lowered for marker in transient_markers
    )


def _terminate_candidate_process(process: subprocess.Popen[str]) -> None:
    """Terminate and reap a candidate's complete process group after timeout."""
    if process.poll() is not None:
        return
    if os.name == "posix":
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(process.pid, signal.SIGTERM)
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            process.terminate()
    try:
        process.wait(timeout=1.0)
    except (subprocess.TimeoutExpired, OSError):
        pass
    if os.name == "posix":
        # The group leader may exit after TERM while an SSH/rsync descendant
        # remains. Always issue the hard kill; a vanished group is harmless.
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(process.pid, signal.SIGKILL)
    elif process.poll() is None:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            process.kill()
    try:
        process.wait(timeout=5.0)
    except (subprocess.TimeoutExpired, OSError):
        if os.name != "posix":
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            process.wait(timeout=1.0)


def _run_candidate_compile(
    command: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout_s: float
) -> CandidateCompileResult | subprocess.CompletedProcess[str]:
    """Run remote Lean in an isolated process group and convert timeout to data."""
    # Existing callers/tests inject subprocess.run to model a compiler. Keep
    # that seam intact; the real subprocess path below owns the process group.
    if subprocess.run is not _ORIGINAL_SUBPROCESS_RUN:
        try:
            return subprocess.run(
                list(command),
                cwd=str(cwd),
                check=False,
                text=True,
                capture_output=True,
                timeout=timeout_s,
                start_new_session=(os.name == "posix"),
                env=dict(env),
            )
        except subprocess.TimeoutExpired:
            return CandidateCompileResult(
                returncode=124,
                stderr=f"Lean compilation timed out after {timeout_s:g} seconds",
                timed_out=True,
                timeout_s=timeout_s,
            )
    try:
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        return CandidateCompileResult(returncode=127, stderr=str(exc))
    stdout = ""
    stderr = ""
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _terminate_candidate_process(process)
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            stdout, stderr = process.communicate(timeout=5.0)
        return CandidateCompileResult(
            returncode=124,
            stdout=stdout if isinstance(stdout, str) else "",
            stderr=(
                f"Lean compilation timed out after {timeout_s:g} seconds"
                + (f"\n{stderr}" if isinstance(stderr, str) and stderr else "")
            ),
            timed_out=True,
            timeout_s=timeout_s,
        )
    return CandidateCompileResult(
        returncode=int(process.returncode or 0),
        stdout=stdout or "",
        stderr=stderr or "",
        timeout_s=timeout_s,
        remote_compile_receipt=_remote_compile_receipt(stdout, stderr),
    )


def _effective_statement_models(
    *, planner_model: str, generator_model: str, judge_model: str
) -> tuple[str, str, str]:
    """Resolve all bounded statement roles to one explicit model by default."""
    requested = next(
        (
            str(value).strip()
            for value in (generator_model, planner_model, judge_model)
            if str(value or "").strip()
        ),
        DEFAULT_BOUNDED_STATEMENT_MODEL,
    )
    return (
        str(planner_model or "").strip() or requested,
        str(generator_model or "").strip() or requested,
        str(judge_model or "").strip() or requested,
    )


def _type_context_guidance() -> str:
    """Return strict typing guidance for notation that Lean cannot infer safely."""
    return (
        "Make type context explicit for every overloaded mathematical expression: "
        "bind real variables with `: ℝ` (and annotate real numerals when needed); "
        "keep `ℝ`, `NNReal`, and `ENNReal` distinct (use these declaration names "
        "instead of composite glyphs such as `ℝ≥0` or `ℝ≥0∞`, which can parse as "
        "the comparison `ℝ ≥ 0`); cast before subtraction or comparison (for example "
        "`(t : ℝ) - (s : ℝ)`), and use `Real.toNNReal` only when a nonnegative-real "
        "variance is required; "
        "bind finite indices with `i : Fin n` or an explicitly typed finite set; "
        "give matrices their dimensions and scalar type, e.g. `Matrix m n ℝ`; "
        "and write finite sums with an explicit index/domain and result type. "
        "For every nontrivial analytic API, verify the exact declaration name, "
        "namespace, signature, and explicit versus implicit binders from retrieved "
        "interfaces before using it. Do not replace a source-defined norm or predicate "
        "with a different Mathlib notion merely because it compiles: preserve and use "
        "a local mathematical definition when the source requires one. Check the actual "
        "typeclass semantics of every notation and operation, rather than relying on "
        "surface syntax or compilation alone. "
        "Use `Type*` or a named universe such as `Type u` for type binders; never "
        "write `Type 0`, `OfNat Type`, or put a numeral in a type position. Do not "
        "rely on inferred numerals, an untyped `Fin`, ambiguous `Matrix`, or a bare `∑`; "
        "a candidate that does not compile must be rejected and its full Lean diagnostic "
        "supplied to the next iteration."
    )


def _api_signature_diagnostic(lean_diagnostic: str) -> str:
    """Add actionable feedback for compiler-reported API resolution failures."""
    diagnostic = str(lean_diagnostic or "").strip()
    lowered = diagnostic.casefold()
    if "unknown identifier" in lowered or "unknown constant" in lowered:
        return (
            "API resolution failure: verify the declaration name and namespace against the "
            "retrieved interfaces. A source-defined local declaration is allowed, but it must "
            "be declared with its mathematical definition before use."
        )
    if any(
        marker in lowered
        for marker in (
            "application type mismatch",
            "function expected at",
            "invalid argument name",
            "failed to synthesize",
        )
    ):
        return (
            "API signature failure: inspect the retrieved declaration signature and supply "
            "only its required explicit arguments; let Lean infer implicit binders unless the "
            "signature requires named arguments. Check each argument's measure, domain, and type."
        )
    return ""


def _missing_source_context_diagnostic(missing: Sequence[str]) -> str:
    """Explain how to unblock a statement that cites unavailable book context."""
    references = ", ".join(str(item) for item in missing if str(item).strip())
    return (
        "source packet is incomplete: resolve these cited declarations before paid "
        f"statement generation: {references}. Supply the original book/PDF or QA "
        "context for each reference; do not invent a proposition or silently restate "
        "the exercise without it."
    )


# A source entry with no mathematical content cannot become a Lean statement no
# matter which model drafts it. Detecting that deterministically keeps prose
# asides ("why is this a semidefinite program?") from consuming a paid generator
# call and from being scored as a statement-quality failure.
_MATH_CONTENT_RE = re.compile(
    r"\$|\\\(|\\\[|\\begin\{(?:equation|align|gather|multline|eqnarray|array|cases)|"
    r"\\frac|\\sum|\\int|\\prod|\\forall|\\exists|\\leq|\\geq|\\subseteq|\\in\b|"
    r"\\mathbb|\\mathcal|\\operatorname|\\norm|\\lVert|\\langle"
)


def statement_scope_preflight(statement: str, *, kind: str = "") -> str:
    """Return why a source entry cannot yield a Lean statement, or "" when it can.

    This is a deterministic admission gate, not a quality judgement. It runs
    before any paid model call so that unformalizable prose is recorded as
    out-of-scope instead of being billed and then counted as a statement
    failure. Only entries with no mathematical content at all are rejected;
    anything carrying formulas stays in scope regardless of its environment
    kind.
    """
    source = str(statement or "").strip()
    if not source:
        return "source entry has no statement text"
    if _MATH_CONTENT_RE.search(source):
        return ""
    # A rhetorical aside poses no proposition at all, whatever its kind.
    if re.match(r"^\s*\(?\s*(?:again|why|observe|compare|see)\b", source, re.I):
        return "source entry is a prose aside rather than a mathematical claim"
    normalized = str(kind or "").strip().lower()
    # A formula-free entry of an inherently discursive kind carries commentary
    # rather than a formal claim. Kinds that normally assert something
    # (theorem, lemma, definition, exercise, ...) stay in scope even without
    # LaTeX, because they can be stated in words.
    if normalized in {"remark", "conjecture", "notation", "algorithm"}:
        return (
            f"source entry is a formula-free {normalized} with no mathematical content to "
            "formalize; it carries no formal claim"
        )
    return ""


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
_EQUATION_REFERENCE_RE = re.compile(
    r"\b(?:equations?|eqs?\.?|inequalit(?:y|ies)|formulae?|identit(?:y|ies)|bounds?|estimates?)"
    r"\s*\$?\(?\s*(\d+(?:\.\d+)+)\s*\)?\$?"
    r"|\b(?:by|from|using|use|see|in|of)\s+\$?\((\d+(?:\.\d+)+)\)\$?"
    r"|\$?\((\d+(?:\.\d+)+)\)\$?\s+(?:holds?|implies|gives|yields|applies|follows)",
    flags=re.IGNORECASE,
)


def source_references(statement: str) -> tuple[str, ...]:
    """Return stable printed-book references required by a source exercise."""
    text = str(statement or "")
    local_labels = set(re.findall(r"\\(?:tag|label)\{(\d+(?:\.\d+)+)\}", text))
    for display in re.findall(r"\$\$(.*?)\$\$|\\\[(.*?)\\\]", text, flags=re.DOTALL):
        block = next((part for part in display if part), "")
        local_labels.update(re.findall(r"\((\d+(?:\.\d+)+)\)\s*$", block))
    references = [f"{kind.title()} {number}" for kind, number in _SOURCE_REFERENCE_RE.findall(text)]
    for match in _EQUATION_REFERENCE_RE.finditer(text):
        number = next(part for part in match.groups() if part)
        if number not in local_labels:
            references.append(f"Equation {number}")
        continuation = re.match(
            r"(?:\s*(?:,|and|or)\s*\$?\(\d+(?:\.\d+)+\)\$?)+", text[match.end() :]
        )
        if continuation:
            for following in re.findall(r"\((\d+(?:\.\d+)+)\)", continuation.group()):
                if following not in local_labels:
                    references.append(f"Equation {following}")
    for number in re.findall(r"\\eqref\{(\d+(?:\.\d+)+)\}", text):
        if number not in local_labels:
            references.append(f"Equation {number}")
    return tuple(dict.fromkeys(references))


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
        if reference.startswith("Equation "):
            number = re.escape(reference.removeprefix("Equation "))
            # A displayed label ends a formula line (or stands on its own).
            # Ordinary prose mentions such as “inequality (5.7) holds” are not
            # definitions and must not satisfy missing-source admission.
            for equation in re.finditer(rf"(?m)^[^\n]*\({number}\)[ \t]*$", text):
                prefix = equation.group().rsplit("(", 1)[0]
                if re.search(
                    r"\b(?:equation|inequality|formula|by|from|see|using)\b", prefix, re.IGNORECASE
                ):
                    continue
                page_start = text.rfind("\f", 0, equation.start()) + 1
                lower = max(page_start, equation.start() - max_chars // 2)
                begin = text.rfind("\n\n", lower, equation.start())
                begin = begin + 2 if begin >= lower else lower
                preceding_heading = next(
                    (start for start in reversed(starts) if lower <= start <= equation.start()),
                    None,
                )
                if preceding_heading is not None:
                    begin = preceding_heading
                end = text.find("\n\n", equation.end())
                end = len(text) if end < 0 else end
                end = min(
                    end, next((start for start in starts if start > equation.end()), len(text))
                )
                context = text[begin : min(end, begin + max_chars)].strip()
                if re.search(r"[=<>≤≥∑∫‖∥]|\\(?:leq?|geq?|sum|int)\b", context):
                    contexts[reference] = context
                    break
            continue
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
        begin = match.start()
        if reference.startswith("Remark "):
            # Remarks such as 4.7.3 inherit K, Sigma and sampling assumptions
            # from the preceding theorem. Include that local section context.
            number = reference.removeprefix("Remark ")
            section = number.rsplit(".", 1)[0]
            prerequisites = list(
                re.finditer(
                    rf"(?im)^\s*(?:Theorem|Proposition|Lemma|Definition)\s+{re.escape(section)}\.\d+\b",
                    text[max(0, begin - max_chars // 2) : begin],
                )
            )
            if prerequisites:
                offset = max(0, begin - max_chars // 2)
                begin = max(offset, offset + prerequisites[-1].start() - 1200)
        context = text[begin : min(end, begin + max_chars)].strip()
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
    # HDP's QA JSON lives in source/, while the actual book is in source/full/.
    # Prefer this fixed book location, not an arbitrary recursive PDF search.
    for parent in (source_file.parent, *list(source_file.parents)[:4]):
        if parent.name == "source" and parent.parent.name == "HDP":
            hdp_book = parent / "full" / "HDP-2.pdf"
            if hdp_book.is_file():
                pdfs = [hdp_book]
                break
    for parent in (source_file.parent, *list(source_file.parents)[:4]):
        if pdfs:
            break
        pdfs.extend(sorted(parent.glob("*.pdf")))
        if pdfs:
            break
    book_text = (
        _source_pdf_text(pdfs[0], source_file=source_file, timeout_s=timeout_s) if pdfs else ""
    )
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
    # Structured source-fidelity declarations are optional for low-risk legacy
    # items, but become mandatory for probability/time-process statements.
    source_contract: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CampaignStatementSource:
    """One item-level source slice shared by bounded and escalation lanes."""

    label: str
    statement: str
    proof: str
    kind: str
    source_file: Path
    source_relative: str


def _required_source_contract_fields(statement: str) -> tuple[str, ...]:
    """Return contract fields needed to interpret high-risk source semantics."""
    lower = str(statement or "").lower()
    fields: list[str] = []
    probability = bool(
        re.search(
            r"brownian|random variable|probabil|measure|expectation|mgf|gaussian|wiener",
            lower,
        )
    )
    time_process = bool(
        re.search(r"brownian|running supremum|supremum|for every .*t|t_?0|t₀|process", lower)
    )
    if probability:
        fields.extend(
            (
                "objects",
                "domains",
                "hypotheses",
                "conclusion",
                "measure_space",
                "measurability",
                "integrability",
            )
        )
    if time_process:
        fields.append("time_domain")
    return tuple(dict.fromkeys(fields))


def _source_requires_nonnegative_time(statement: str) -> bool:
    """Recognize the common running-process domain ``0 <= t <= t₀``."""
    lower = str(statement or "").lower()
    return bool(
        re.search(r"brownian", lower)
        and re.search(r"running\s+supremum|\\sup|\bsupremum\b", lower)
        and re.search(r"t\s*(?:\\leq|\\le|<=|≤)\s*t\s*_?0|t₀", lower)
    )


def statement_contract_lint(statement: str, draft: StatementDraft) -> tuple[str, ...]:
    """Return deterministic source-contract violations before Lean compilation.

    This is intentionally conservative: legacy low-risk statements keep their
    existing payload shape, while probability/time-process items must provide
    explicit semantic fields. The checks are lexical guards, not a replacement
    for independent mathematical review.
    """
    issues: list[str] = []
    contract = {
        str(key).strip(): str(value).strip()
        for key, value in dict(draft.source_contract or {}).items()
        if str(key).strip()
    }
    missing = [
        field_name
        for field_name in _required_source_contract_fields(statement)
        if not contract.get(field_name)
        or contract[field_name].casefold() in {"none", "n/a", "unknown", "[none]"}
    ]
    if missing:
        issues.append("source contract is missing required fidelity fields: " + ", ".join(missing))

    code = _strip_lean_comments_and_strings(draft.lean_code)
    issues.extend(declaration_contract_issues(draft.lean_code))

    if _source_requires_nonnegative_time(statement) and re.search(r"\bSet\.Iic\b|\bIic\b", code):
        issues.append(
            "time-domain mismatch: source running supremum is over 0 ≤ t ≤ t₀, "
            "but candidate uses Set.Iic t₀ (which includes negative times)"
        )

    return tuple(dict.fromkeys(issues))


def lint_generated_statement_contract(
    statement: str,
    lean_code: str,
    *,
    source_contract: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Lint a generated Lean candidate using the bounded lane contract rules."""
    draft = StatementDraft(
        lean_code=str(lean_code or ""),
        declarations=(),
        source_qualifiers="",
        scope_changes="",
        proof_notes="",
        source_contract=dict(source_contract or {}),
    )
    return statement_contract_lint(statement, draft)


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
        r"\b(?:admit|axiom|opaque|set_option\s+maxRecDepth|unsafe)\b", sanitized_code
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
    # A theorem may be the final declaration in a namespace.  Treat its
    # closing `end` as a body boundary as well; otherwise `end Namespace` is
    # incorrectly considered part of an otherwise valid `by sorry` body.
    declaration_boundaries = sorted(
        [match.start() for match in declaration_starts]
        + [match.start() for match in re.finditer(r"(?m)^\s*end\b[^\n]*$", sanitized_code)]
    )
    theorem_bodies: list[str] = []
    for declaration in declaration_starts:
        if not re.match(r"\s*(?:private\s+)?(?:theorem|lemma)\b", declaration.group()):
            continue
        end = next(
            (boundary for boundary in declaration_boundaries if boundary > declaration.start()),
            len(sanitized_code),
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
    raw_contract = payload.get("source_contract", {})
    if raw_contract is None:
        raw_contract = {}
    if not isinstance(raw_contract, Mapping):
        raise BoundedStatementRefinementError("source_contract must be a JSON object")
    source_contract = {
        str(key).strip(): str(value).strip()
        for key, value in raw_contract.items()
        if str(key).strip() and str(value).strip()
    }
    return StatementDraft(
        lean_code=lean_code.rstrip() + "\n",
        declarations=names,
        source_qualifiers=str(payload.get("source_qualifiers", "") or "none").strip(),
        scope_changes=str(payload.get("scope_changes", "") or "none").strip(),
        proof_notes=str(payload.get("proof_notes", "") or "source proof only").strip(),
        source_contract=source_contract,
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
        "pricing_known": bool(result.pricing_known),
        "cost_source": result.cost_source or "cost_unavailable",
    }


def _source_statement(
    source_file: Path, *, labels: Sequence[str] = ()
) -> tuple[str, str, str, str]:
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
    kind = str(item.get("kind", "") or "").strip().lower()
    if not statement:
        raise BoundedStatementRefinementError("source item has no statement")
    return label, statement[:16000], proof[:12000], kind


def campaign_statement_source(
    campaign_path: str | Path,
    *,
    project_root: str | Path,
    batch_id: str,
) -> CampaignStatementSource | None:
    """Return a selected QA source slice, or ``None`` for non-QA batches.

    The full escalation lane must use the same source item as bounded
    refinement.  This helper deliberately performs no model call or Lean
    invocation, so callers can run source admission before spending either.
    """
    root = Path(project_root).expanduser().resolve()
    campaign = read_campaign(Path(campaign_path).expanduser().resolve())
    batch = next(
        (
            item
            for item in campaign.get("batches", []) or []
            if isinstance(item, Mapping) and str(item.get("id", "") or "") == batch_id
        ),
        None,
    )
    if not isinstance(batch, Mapping):
        raise BoundedStatementRefinementError(f"unknown campaign batch: {batch_id}")
    source_relative = str(batch.get("source_file", campaign.get("source", "")) or "").strip()
    if not source_relative or Path(source_relative).suffix.lower() != ".json":
        return None
    source_file = (root / source_relative).resolve()
    if not source_file.is_relative_to(root):
        raise BoundedStatementRefinementError("campaign source path escapes project root")
    # Escalated full-tool formalization may be launched from a campaign action
    # whose source is a document or a test-only placeholder rather than the QA
    # JSON consumed by the bounded lane.  Source admission is an optional
    # preflight for that lane; a missing file must therefore fall through to
    # the full formalize process instead of raising before it starts.
    if not source_file.is_file():
        return None
    labels = tuple(str(value) for value in batch.get("labels", []) or [])
    label, statement, proof, kind = _source_statement(source_file, labels=labels)
    return CampaignStatementSource(
        label=label,
        statement=statement,
        proof=proof,
        kind=kind,
        source_file=source_file,
        source_relative=source_relative,
    )


def campaign_statement_source_admission(
    campaign_path: str | Path,
    *,
    project_root: str | Path,
    batch_id: str,
    timeout_s: int = 120,
) -> tuple[CampaignStatementSource | None, tuple[str, ...]]:
    """Run bounded-lane source admission without calling a provider or Lean."""
    try:
        source = campaign_statement_source(
            campaign_path,
            project_root=project_root,
            batch_id=batch_id,
        )
    except (OSError, ValueError, BoundedStatementRefinementError) as exc:
        # Escalation is also used for test/non-QA campaigns. A malformed or
        # absent QA packet must not prevent the full-tool formalize process from
        # starting; only an admitted packet is subject to bounded admission.
        message = str(exc).lower()
        if "escapes project root" in message or "unknown campaign batch" in message:
            raise
        return None, ()
    if source is None:
        return None, ()
    issue = statement_scope_preflight(source.statement, kind=source.kind)
    if issue:
        return source, (issue,)
    if source_reference_context_required(source.statement):
        _contexts, missing = resolve_source_reference_context(
            source.statement,
            source_file=source.source_file,
            timeout_s=timeout_s,
        )
        if missing:
            return source, (_missing_source_context_diagnostic(missing),)
    return source, ()


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
                    for key in (
                        "name",
                        "module",
                        "statement",
                        "file",
                        "line",
                        "preview",
                        "match",
                    )
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
        "- Source fidelity contract: "
        f"{json.dumps(dict(draft.source_contract), ensure_ascii=False, sort_keys=True) if draft.source_contract else '[none]'}\n"
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
    warmup_workers: int | None = None,
    timeout_s: int = 120,
    compile_timeout_s: float | int | None = None,
    warm_remote_probe: bool = False,
    model_call: Callable[..., VerificationReviewResult] = run_model_verification_review,
    search_call: Callable[..., Any] = lean_search,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run and account one bounded statement action, optionally using remote warm LeanProbe."""
    planner_model, generator_model, judge_model = _effective_statement_models(
        planner_model=planner_model,
        generator_model=generator_model,
        judge_model=judge_model,
    )
    root = Path(project_root).expanduser().resolve()
    execution_env = dict(os.environ if environ is None else environ)
    worker_id = str(execution_env.get("LEANFLOW_CAMPAIGN_WORKER_ID", "") or "").strip()
    if worker_id:
        execution_env["LEANFLOW_WORKFLOW_STATE_NAMESPACE"] = worker_id
    execution_env["LEANFLOW_CAMPAIGN_BATCH_ID"] = batch_id
    lean_capacity = execution_env.get(PROJECT_LEAN_CAPACITY_ENV, "1")
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
    label, statement, proof, source_kind = _source_statement(
        source_file,
        labels=tuple(str(value) for value in batch.get("labels", []) or []),
    )
    out_of_scope = statement_scope_preflight(statement, kind=source_kind)
    if out_of_scope:
        # Terminal, not a retry: no model can turn a formula-free prose aside
        # into a Lean statement, so bill nothing and mark the batch skipped so
        # dependent batches are unblocked instead of waiting on it forever.
        diagnostic = f"out of scope for formalization: {out_of_scope}"
        outcome = {
            "stage": "statements",
            "success": False,
            "exit_code": 0,
            "reason": diagnostic,
            "target_file": target_relative,
            "proof_obligations": 0,
            "cost_usd": 0.0,
            "cost_source": "none",
            "cost_scope": "no_provider_call",
            "provenance": "agent",
            "iterations": 0,
            "candidate_attempts": 0,
            "failure_stage": "out_of_scope",
            "failure_class": "out_of_scope",
            "terminal": True,
            "source_kind": source_kind,
            "final_diagnostic": diagnostic,
            "candidate_diagnostics": [{"stage": "out_of_scope", "diagnostic": diagnostic}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        if worker_id:
            outcome["worker_id"] = worker_id
        update_campaign_file(
            campaign_path,
            lambda current: (
                record_campaign_outcome(current, batch_id=batch_id, outcome=outcome),
                None,
            ),
        )
        return outcome
    fidelity_preflight = source_fidelity_preflight(statement)
    reference_contexts, missing_references = resolve_source_reference_context(
        statement,
        source_file=source_file,
        timeout_s=timeout_s,
    )
    if source_reference_context_required(statement) and missing_references:
        diagnostic = _missing_source_context_diagnostic(missing_references)
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
        if worker_id:
            outcome["worker_id"] = worker_id

        def record_incomplete_source(current: Mapping[str, Any]):
            return (
                record_campaign_outcome(current, batch_id=batch_id, outcome=outcome),
                None,
            )

        update_campaign_file(campaign_path, record_incomplete_source)
        return outcome
    reference_context = "\n\n".join(
        f"### {reference}\n{context}" for reference, context in reference_contexts.items()
    )

    previous_outcome = dict(batch.get("last_outcome", {}) or {})
    previous_failure_stage = str(previous_outcome.get("failure_stage", "") or "")
    prior_attempts = list(batch.get("attempts", []) or [])
    is_retry = len(prior_attempts) > 0
    prior_feedback_items: dict[str, list[str]] = {}
    for attempt in reversed(list(batch.get("attempts", []) or []) + [previous_outcome]):
        if not isinstance(attempt, Mapping):
            continue
        if str(attempt.get("stage", "") or "") not in {"", "statements"}:
            continue
        if classify_campaign_retry_class(attempt) == RETRY_CLASS_INFRASTRUCTURE:
            continue
        if str(attempt.get("review_decision", "") or "").upper() == "PASS":
            break
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
    verdict = latest_statement_verdict(batch)
    if str(verdict.get("review_decision", "") or "").upper() == "PASS":
        prior_feedback.pop("semantic_contract", None)
        prior_feedback.pop("semantic_review", None)
    elif verdict:
        prior_feedback["semantic_review"] = "\n\n".join(
            filter(
                None,
                (
                    statement_review_feedback(verdict),
                    prior_feedback.get("semantic_review", ""),
                ),
            )
        )[:6000]

    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    cost_usd = 0.0
    pricing_known = True
    quota_receipts: list[dict[str, Any]] = []
    retrieval_history: list[dict[str, Any]] = []
    planner_fallback_used = False
    last_bad_code = ""
    # Only Lean compilation errors are relevant to retrieval planning; format
    # errors are generator quality issues that retrieval cannot fix.
    lean_compile_error = prior_feedback.get("lean_compilation", "")[:6000]
    format_error = prior_feedback.get("format_check", "")[:6000]
    compile_error = "\n\n".join(filter(None, [format_error, lean_compile_error]))[:6000]
    semantic_feedback = "\n\n".join(
        filter(
            None,
            [
                prior_feedback.get("semantic_contract", ""),
                prior_feedback.get("semantic_review", ""),
            ],
        )
    )[:6000]
    final_draft: StatementDraft | None = None
    review_evidence_path: Path | None = None
    final_review = ""
    iterations = 0
    candidate_attempts = 0
    failure_stage = "not_started"
    infrastructure_failure = False
    candidate_diagnostics: list[dict[str, Any]] = []
    remote_compile_receipts: list[dict[str, Any]] = []
    candidate_count = max(1, min(8, int(candidates_per_iteration or 1)))
    pool_workers = max(1, min(candidate_count, int(candidate_workers or 1)))
    candidate_compile_timeout_s = _resolve_candidate_compile_timeout(
        compile_timeout_s,
        fallback=timeout_s,
    )
    planner_timeout_s = _resolve_retrieval_planner_timeout(
        execution_env.get(RETRIEVAL_PLANNER_TIMEOUT_ENV), total_timeout_s=timeout_s
    )
    usage_lock = Lock()
    # ``lake env lean`` re-execs lean from PATH, and an elan toolchain under
    # <root>/.elan-home is on neither PATH nor the caller's env.  Publish the
    # discovered bin directory and resolve a bare executable name against it so
    # a missing toolchain cannot surface as FileNotFoundError only after the
    # paid generator call has already run.
    compile_env = add_lean_toolchain_env(execution_env, project_root=root)
    # ``remote-bin/lake`` runs on a separate host, so the local subprocess
    # timeout cannot by itself stop its descendants. Publish the resolved,
    # hard-capped value for the wrapper to enforce server-side as well.
    compile_env[REMOTE_LEAN_TIMEOUT_ENV] = f"{candidate_compile_timeout_s:g}"
    if not Path(lake_executable).is_absolute():
        lean_bin = discover_lean_bin(root)
        if lean_bin is not None and os.access(lean_bin / lake_executable, os.X_OK):
            lake_executable = str(lean_bin / lake_executable)

    # This is deliberately opt-in: the normal remote-bin/lake path remains the
    # compatibility and final-gate path.  A failed warm startup falls back to
    # that path, so an unavailable server never changes campaign accounting.
    warm_probe: RemoteWarmProbe | None = None
    if warm_probe_enabled(warm_remote_probe):
        try:
            warm_probe = RemoteWarmProbe(root, warmup_workers=warmup_workers)
            with acquire_project_lean_capacity(root, capacity=lean_capacity):
                warm_probe.start()
        except Exception as exc:
            candidate_diagnostics.append(
                {
                    "stage": "warm_probe",
                    "status": "unavailable",
                    "diagnostic": str(exc)[:1000],
                }
            )
            if warm_probe is not None:
                warm_probe.close(force=True)
            warm_probe = None

    # Every role in this lane is a model call, so an unset/"auto"/"local"
    # setting must resolve to a real backend. Without this, the configured
    # default for autoformalizer_verification ("local", the deterministic Lean
    # checks) reaches the model dispatcher and fails the whole action at the
    # first planner call with "No LLM provider configured ... provider=local".
    default_provider = provider or "auto"
    effective_fallback_provider = (
        resolve_model_verification_provider(
            AUTOFORMALIZER_VERIFICATION_TASK, generator_fallback_provider
        )
        if generator_fallback_provider
        else ""
    )
    effective_planner_provider = resolve_model_verification_provider(
        AUTOFORMALIZER_VERIFICATION_TASK, planner_provider or default_provider
    )
    effective_generator_provider = resolve_model_verification_provider(
        AUTOFORMALIZER_VERIFICATION_TASK, generator_provider or default_provider
    )
    effective_judge_provider = resolve_model_verification_provider(
        BLUEPRINT_VERIFICATION_TASK, judge_provider or default_provider
    )

    def call_model(
        *,
        task: str,
        prompt: str,
        system_prompt: str,
        model: str,
        max_tokens: int,
        call_provider: str,
        timeout_override_s: float | None = None,
    ) -> VerificationReviewResult:
        nonlocal cost_usd, pricing_known
        try:
            if execution_env.get("LEANFLOW_PROVIDER_QUOTA_BUDGET_PATH"):
                # The verifier dispatcher reads process environment. Refuse a
                # mismatched thread overlay instead of silently paying retries
                # beyond the one-request quota reservation.
                for name in (
                    "LEANFLOW_AUXILIARY_RETRY_COUNT",
                    "LEANFLOW_AUXILIARY_TIMEOUT_RETRY_COUNT",
                ):
                    if execution_env.get(name) != "0" or os.environ.get(name) != "0":
                        raise ProviderQuotaError(
                            "quota guard requires actual provider retries disabled"
                        )
            reservation = reserve_provider_request(
                prompt=prompt,
                system_prompt=system_prompt,
                max_output_tokens=max_tokens,
                base_url=str(execution_env.get("LEANFLOW_OPENAI_BASE_URL", "")),
                model=model,
                environ=execution_env,
            )
        except ProviderQuotaError as exc:
            return VerificationReviewResult(
                task=task,
                provider=call_provider,
                model=model,
                mode="model",
                status="unavailable",
                response="",
                command=[],
                exit_status=None,
                truncated=False,
                response_chars=0,
                max_response_chars=0,
                error=f"provider quota guard: {exc}",
                failure_class="budget_limit",
            )
        try:
            previous_reservation_id = os.environ.get("LEANFLOW_PROVIDER_QUOTA_RESERVATION_ID")
            if reservation is not None:
                os.environ["LEANFLOW_PROVIDER_QUOTA_RESERVATION_ID"] = reservation.reservation_id
            result = model_call(
                provider=call_provider,
                model=model,
                task=task,
                prompt=prompt,
                system_prompt=system_prompt,
                timeout_s=(timeout_s if timeout_override_s is None else timeout_override_s),
                max_tokens=max_tokens,
            )
        except BaseException:
            if reservation is not None:
                reservation.settle(prompt_tokens=0, completion_tokens=0, status="exception")
            raise
        finally:
            if reservation is not None:
                if previous_reservation_id is None:
                    os.environ.pop("LEANFLOW_PROVIDER_QUOTA_RESERVATION_ID", None)
                else:
                    os.environ["LEANFLOW_PROVIDER_QUOTA_RESERVATION_ID"] = previous_reservation_id
        if reservation is not None:
            receipt = reservation.settle(
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                status=result.status,
            )
            with usage_lock:
                quota_receipts.append({"reservation_id": reservation.reservation_id, **receipt})
        measured = _result_usage(result)
        with usage_lock:
            cost_usd += float(measured["cost_usd"])
            pricing_known = pricing_known and bool(measured["pricing_known"])
            for key in usage:
                usage[key] += int(measured[key])
        return result

    for iteration in range(1, max(1, min(3, max_iterations)) + 1):
        iterations = iteration
        # Fast-path optimization: skip retrieval planner on first iteration of a
        # fresh (non-retry) batch unless there's already Lean compilation feedback
        # or semantic review feedback that suggests we need Mathlib context. Real
        # data shows 86% of successful items need zero retrieval queries, so the
        # planner call is wasted work for most items. Format errors are generator
        # quality issues that retrieval cannot fix, so they don't trigger retrieval.
        # Don't skip if user explicitly specified a planner_provider (they want to
        # test or use a specific planner backend), or if this is a retry attempt
        # (better safe than sorry on retry).
        skip_retrieval = (
            iteration == 1
            and not is_retry
            and not lean_compile_error
            and not semantic_feedback
            and not retrieval_history
            and not planner_provider
        )

        if skip_retrieval:
            # First attempt with no prior feedback: go straight to generation
            retrieval_context = ""
        else:
            # Retry attempt or we have feedback that suggests we need Mathlib help
            planner = call_model(
                task=AUTOFORMALIZER_VERIFICATION_TASK,
                call_provider=effective_planner_provider,
                model=planner_model,
                max_tokens=DEFAULT_RETRIEVAL_PLANNER_MAX_TOKENS,
                system_prompt="You are a Lean 4 retrieval planner. Return only concise search queries.",
                prompt=(
                    "Return zero to three essential Lean/Mathlib search queries in one plain code fence. "
                    "Do not solve or formalize the theorem. Do not repeat prior queries.\n\n"
                    f"STATEMENT\n{statement[:6000]}\n\nCOMPILER ERROR\n{compile_error[:2000] or '[none]'}\n\n"
                    f"SEMANTIC FEEDBACK\n{semantic_feedback[:2000] or '[none]'}\n\n"
                    f"PRIOR QUERIES\n{str([item['query'] for item in retrieval_history])[:1000]}"
                ),
                timeout_override_s=planner_timeout_s,
            )
            if planner.status != "ok":
                diagnostic = planner.error or "retrieval planner provider failed"
                if _planner_failure_is_transient(planner):
                    # Retrieval is an optional hint. Preserve the provider
                    # reservation as unknown through call_model, record the
                    # transport failure, and let generation/compile/review
                    # continue deterministically without search context.
                    candidate_diagnostics.append(
                        {
                            "stage": "planner_unavailable",
                            "status": planner.status,
                            "failure_class": planner.failure_class,
                            "diagnostic": diagnostic[:2000],
                            "fallback": "empty_retrieval_context",
                        }
                    )
                    planner_fallback_used = True
                    retrieval_context = ""
                else:
                    infrastructure_failure = True
                    failure_stage = "retrieval_planner"
                    semantic_feedback = diagnostic
                    break
            if cost_usd >= reserve_usd:
                infrastructure_failure = True
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
                    {
                        "query": query,
                        "results": list(payload.get("results", []) or [])[:2],
                    }
                )
            retrieval_context = _format_retrieval_context(retrieval_history)
        generation_prompt = (
            "Produce exactly one JSON object with keys lean_code, declarations (array), "
            "source_qualifiers, scope_changes, proof_notes, source_contract (object). "
            "For every field named by the source-fidelity contract, write a non-empty string in "
            "source_contract; do not use `none`, `unknown`, or `[none]`. lean_code must be a complete Lean file "
            "whose theorem bodies are `by sorry`. Preserve every quantifier and condition. Do not output "
            "proof steps. Never use standalone `λ` as an identifier; use `coeff` instead because Lean "
            "reserves `λ` as lambda syntax. Use the retrieved interfaces only when relevant.\n\n"
            f"EXPLICIT TYPE-CONTEXT REQUIREMENTS\n{_type_context_guidance()}\n\n"
            f"SOURCE\n{statement}\n\nREFERENCE PROOF (HINT ONLY)\n{proof}\n\n"
            f"RESOLVED BOOK REFERENCES\n{reference_context or '[none]'}\n\n"
            f"DETERMINISTIC SOURCE-FIDELITY PREFLIGHT\n{fidelity_preflight}\n\n"
            f"REQUIRED SOURCE CONTRACT FIELDS\n{', '.join(_required_source_contract_fields(statement)) or '[none]'}\n\n"
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
            and effective_fallback_provider
            and effective_fallback_provider != effective_generator_provider
            and cost_usd < reserve_usd
        ):
            generated_results.append(
                call_model(
                    task=AUTOFORMALIZER_VERIFICATION_TASK,
                    call_provider=effective_fallback_provider,
                    model=generator_fallback_model or generator_model,
                    max_tokens=5000,
                    system_prompt="You translate mathematical statements to Lean 4 signatures only; never prove them.",
                    prompt=generation_prompt,
                )
            )
            candidate_attempts += 1
        if not any(result.status == "ok" for result in generated_results):
            infrastructure_failure = True
            failure_stage = "statement_generation"
            semantic_feedback = next(
                (result.error for result in generated_results if result.error),
                "statement generator provider failed",
            )
            break
        if cost_usd >= reserve_usd:
            infrastructure_failure = True
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

        # Run deterministic semantic guards before touching the remote Lean
        # worker. A candidate that violates the source contract must be
        # regenerated with the structured findings as feedback; compilation
        # cannot establish source fidelity.
        contract_drafts: list[StatementDraft] = []
        contract_feedback: list[str] = []
        for draft in drafts:
            violations = statement_contract_lint(statement, draft)
            if violations:
                diagnostic = "; ".join(violations)
                contract_feedback.append(diagnostic)
                candidate_diagnostics.append(
                    {
                        "stage": "semantic_contract",
                        "status": "blocked",
                        "diagnostic": diagnostic[:2000],
                    }
                )
                continue
            contract_drafts.append(draft)
        if not contract_drafts:
            failure_stage = "semantic_contract"
            compile_error = ""
            semantic_feedback = "\n\n".join(dict.fromkeys(contract_feedback))[:6000]
            continue
        drafts = contract_drafts

        target.parent.mkdir(parents=True, exist_ok=True)

        def compile_draft(draft: StatementDraft):
            checked_code = candidate_signature_probe(draft.lean_code)
            if warm_probe is not None:
                try:
                    with acquire_project_lean_capacity(root, capacity=lean_capacity):
                        response = warm_probe.check(checked_code, candidate_compile_timeout_s)
                    has_errors = bool(response.get("has_errors"))
                    return draft, CandidateCompileResult(
                        returncode=(1 if has_errors or not response.get("success", False) else 0),
                        stdout=str(response.get("output", "") or ""),
                        stderr=str(response.get("error", "") or ""),
                        timed_out=bool(response.get("timed_out")),
                        timeout_s=candidate_compile_timeout_s,
                    )
                except TimeoutError as exc:
                    return draft, CandidateCompileResult(
                        returncode=124,
                        stderr=str(exc),
                        timed_out=True,
                        timeout_s=candidate_compile_timeout_s,
                    )
                except Exception:
                    # A transient service failure is safe to retry through the
                    # existing remote Lake wrapper for this candidate.
                    warm_probe.close(force=True)
            candidate = target.with_name(f"StatementCandidate_{uuid.uuid4().hex}.lean")
            candidate.write_text(checked_code, encoding="utf-8")
            try:
                with acquire_project_lean_capacity(root, capacity=lean_capacity):
                    completed = _run_candidate_compile(
                        [
                            lake_executable,
                            "env",
                            "lean",
                            str(candidate.relative_to(root)),
                        ],
                        cwd=root,
                        timeout_s=candidate_compile_timeout_s,
                        # Keep toolchain discovery and worker namespace local to
                        # this action; sibling threads must never mutate os.environ.
                        env=compile_env,
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
        remote_compile_receipts = [
            dict(receipt)
            for _draft, result in compiled
            for receipt in [getattr(result, "remote_compile_receipt", None)]
            if isinstance(receipt, Mapping)
        ]
        compilation_outputs = {
            draft.lean_code: (result.stdout or "") + (result.stderr or "")
            for draft, result in compiled
            if result.returncode == 0
        }
        for _draft, result in compile_failures[:2]:
            timed_out = bool(getattr(result, "timed_out", False))
            diagnostic = (
                f"Lean compilation timed out after {candidate_compile_timeout_s:g} seconds"
                if timed_out
                else (result.stderr or result.stdout or "Lean compilation failed")[-1000:]
            )
            entry: dict[str, Any] = {
                "stage": "lean_compilation",
                "status": "timeout" if timed_out else "failed",
                "diagnostic": diagnostic,
            }
            if timed_out:
                entry["timeout_s"] = candidate_compile_timeout_s
            candidate_diagnostics.append(entry)
        if not compilable_drafts:
            failure_stage = "lean_compilation"
            last_bad_code = compile_failures[0][0].lean_code[:24000]
            compile_error = "\n\n".join(
                (result.stderr or result.stdout or "Lean compilation failed")[-3000:]
                for _draft, result in compile_failures[:2]
            )[:6000]
            semantic_feedback = _api_signature_diagnostic(compile_error)
            continue

        review_feedback: list[str] = []
        for draft in compilable_drafts:
            if cost_usd >= reserve_usd:
                infrastructure_failure = True
                failure_stage = "budget_before_semantic_review"
                semantic_feedback = "reserved cost exhausted before semantic review"
                break
            review_prompt = (
                "Start with exactly PASS or BLOCK. PASS only for a bidirectionally faithful Lean statement: "
                "same objects, domains, quantifier order, hypotheses, conclusion, and edge cases. Ignore sorry. "
                "Check notation semantics and give concise correction feedback.\n\n"
                f"SOURCE\n{statement}\n\nLEAN\n{draft.lean_code}"
                f"\n\n{compilation_review_context(draft.lean_code, compilation_outputs[draft.lean_code])}"
                f"\n\nRESOLVED BOOK REFERENCES\n{reference_context or '[none]'}"
                f"\n\nDETERMINISTIC SOURCE-FIDELITY PREFLIGHT\n{fidelity_preflight}"
                f"\n\nPRIOR SEMANTIC FEEDBACK\n{semantic_feedback or '[none]'}"
            )
            evidence_path, source_digest, candidate_digest = _persist_statement_review_evidence(
                root,
                batch_id=batch_id,
                source_relative=source_relative,
                target_relative=target_relative,
                statement=statement,
                proof=proof,
                draft=draft,
                review_prompt=review_prompt,
            )
            review_evidence_path = evidence_path
            review = call_model(
                task=BLUEPRINT_VERIFICATION_TASK,
                call_provider=effective_judge_provider,
                model=judge_model,
                max_tokens=2500,
                system_prompt="You are an independent source-fidelity judge, not a prover.",
                prompt=review_prompt,
            )
            final_review = review.response
            if review.status != "ok":
                infrastructure_failure = True
                review_feedback.append(review.error or "independent reviewer provider failed")
                break
            if (
                review.status == "ok"
                and _verification_review_decision({"response": review.response}) == "PASS"
            ):
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
        if infrastructure_failure:
            break
    if warm_probe is not None:
        warm_probe.close()
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
    integration = project_target_reachability(root, target_relative)
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
        # A standalone candidate may compile while remaining absent from the
        # project's public import DAG.  Keep this explicit in the ledger so
        # statement completion is never mistaken for integrated coverage.
        "root_reachable": integration["root_reachable"],
        "integration_status": integration["integration_status"],
        "integration_root_file": integration["root_file"],
        "proof_obligations": final_draft.lean_code.count("sorry") if final_draft else 0,
        "cost_usd": round(cost_usd, 6),
        "pricing_known": pricing_known,
        "cost_source": "auxiliary_token_usage" if pricing_known else "cost_unavailable",
        "cost_scope": "bounded_statement_pipeline",
        "provenance": "agent",
        "iterations": iterations,
        "candidate_attempts": candidate_attempts,
        "candidates_per_iteration": candidate_count,
        "failure_stage": "" if success else failure_stage,
        "final_diagnostic": ("" if success else (semantic_feedback or compile_error)[:6000]),
        "candidate_diagnostics": candidate_diagnostics[-8:],
        "retry_feedback_source": "+".join(
            stage
            for stage in (
                "semantic_contract",
                "semantic_review",
                "lean_compilation",
                "format_check",
            )
            if stage in prior_feedback
        ),
        "retrieval_queries": [item["query"] for item in retrieval_history],
        "retrieval_planner_timeout_s": planner_timeout_s,
        "retrieval_planner_fallback": planner_fallback_used,
        "statement_providers": {
            "planner": effective_planner_provider,
            "generator": effective_generator_provider,
            "generator_fallback": effective_fallback_provider,
            "judge": effective_judge_provider,
        },
        "review_evidence": (
            str(review_evidence_path.relative_to(root)) if review_evidence_path else ""
        ),
        "review_decision": (
            "PASS"
            if success
            else (
                "BLOCK"
                if not infrastructure_failure
                and failure_stage in {"semantic_contract", "semantic_review"}
                else ""
            )
        ),
        "review_provider": (
            "deterministic_statement_contract_lint"
            if failure_stage == "semantic_contract" and not success
            else effective_judge_provider
        ),
        "review_findings": (
            [semantic_feedback]
            if not success and not infrastructure_failure and semantic_feedback
            else []
        ),
        # Keep telemetry tied to the requested role model; provider responses
        # may advertise a different backend default.
        "model": generator_model,
        "statement_models": {
            "planner": planner_model,
            "generator": generator_model,
            "judge": judge_model,
        },
        "provider": provider,
        "usage": usage,
        "provider_quota_receipts": quota_receipts,
        "remote_compile_receipts": remote_compile_receipts,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if infrastructure_failure:
        outcome["infrastructure_failure"] = True
        outcome["retry_class"] = RETRY_CLASS_INFRASTRUCTURE
        outcome["failure_class"] = "infrastructure"
    if worker_id:
        outcome["worker_id"] = worker_id
    update_campaign_file(
        campaign_path,
        lambda current: (
            record_campaign_outcome(current, batch_id=batch_id, outcome=outcome),
            None,
        ),
    )
    return outcome
