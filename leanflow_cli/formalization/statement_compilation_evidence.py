"""Attach compiler facts and selected API signatures to source-fidelity review."""

from __future__ import annotations

import hashlib
import re

from leanflow_cli.lean.lean_parsing import _strip_lean_comments_and_strings


def candidate_signature_probe(code: str) -> str:
    """Append bounded compiler queries for APIs whose old signatures misled reviewers.

    The original candidate remains the reviewed artifact. Queries are emitted in
    the same remote compilation, so the response reflects its actual toolchain.
    Keep this list explicit: arbitrary dotted terms can be local field notation.
    """
    names = ("NormedSpace.exp", "Matrix.trace")
    terms = _strip_lean_comments_and_strings(code)
    checks = [
        f"#check @_root_.{name}" for name in names if re.search(rf"\b{re.escape(name)}\b", terms)
    ]
    return code + ("\n\n" + "\n".join(checks) + "\n" if checks else "")


def compilation_review_context(code: str, compiler_output: str) -> str:
    """Bind successful elaboration evidence to the exact candidate digest."""
    candidate_digest = hashlib.sha256(code.encode()).hexdigest()
    return (
        "REMOTE LEAN COMPILATION EVIDENCE\n"
        f"Candidate SHA-256: {candidate_digest}\n"
        "Result: PASS. This exact candidate was accepted by the project Lean compiler; "
        "sorry placeholders are allowed only for statement elaboration.\n"
        "Compiler output / checked API signatures:\n"
        f"{compiler_output.strip()[-6000:] or '[no compiler messages]'}\n\n"
        "Review mathematical source fidelity independently. Compilation PASS does not establish "
        "the right mathematics, missing hypotheses, or a proof. Keep BLOCK for genuine semantic "
        "mismatches. Do not issue a semantic BLOCK based only on a guessed typing error, an "
        "assumed explicit argument, or a remembered API signature that contradicts this compiler "
        "evidence. If an elaborated term has the wrong mathematical meaning, explain that meaning "
        "and its concrete mismatch with the source.\n"
    )
