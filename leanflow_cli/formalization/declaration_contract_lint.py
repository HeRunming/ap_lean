"""Detect vacuous statement shapes in independently parsed Lean declarations."""

from __future__ import annotations

import re

from leanflow_cli.lean.lean_parsing import (
    _declaration_line_index_from_text,
    _strip_lean_comments_and_strings,
    _top_level_relation_sides,
    declaration_statement_text,
)

_OPENERS = {"(": ")", "[": "]", "{": "}", "⦃": "⦄", "⟨": "⟩"}
_CLOSERS = set(_OPENERS.values())


def _normalized_formula(value: str) -> str:
    """Normalize spacing and redundant outer parentheses in a small formula."""
    normalized = re.sub(r"\s+", "", value)
    while normalized.startswith("(") and normalized.endswith(")"):
        depth = 0
        for index, char in enumerate(normalized):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
        if depth or index != len(normalized) - 1:
            break
        normalized = normalized[1:-1]
    return normalized


def _top_level_colon(value: str) -> int:
    """Find the result-type colon outside binder annotations."""
    depth = 0
    for index, char in enumerate(value):
        if char in _OPENERS:
            depth += 1
        elif char in _CLOSERS:
            depth = max(0, depth - 1)
        elif char == ":" and depth == 0:
            return index
    return -1


def _binder_hypotheses(header: str) -> set[str]:
    """Collect explicit and implicit binder types without their nested binders."""
    hypotheses: set[str] = set()
    depth = 0
    start = 0
    for index, char in enumerate(header):
        if char in _OPENERS:
            if depth == 0:
                start = index + 1
            depth += 1
        elif char in _CLOSERS and depth:
            depth -= 1
            if depth == 0:
                content = header[start:index]
                colon = _top_level_colon(content)
                if colon >= 0:
                    hypotheses.add(_normalized_formula(content[colon + 1 :]))
    return hypotheses


def _reflexive_formula(formula: str) -> bool:
    """Recognize a top-level reflexive equality or equivalence."""
    sides = _top_level_relation_sides(_normalized_formula(formula))
    return bool(
        sides
        and _normalized_formula(sides[0])
        and _normalized_formula(sides[0]) == _normalized_formula(sides[1])
    )


def declaration_contract_issues(lean_code: str) -> tuple[str, ...]:
    """Lint declaration statements independently of their proof bodies.

    The shared parser owns declaration boundaries and assignment recognition.
    Definition bodies describe predicates; theorem headers describe claims.
    A function returning its input is therefore never a circular theorem.
    """
    issues: list[str] = []
    code = _strip_lean_comments_and_strings(lean_code)
    for entry in _declaration_line_index_from_text(code):
        kind = entry["kind"]
        if kind not in {"theorem", "lemma", "example", "def", "abbrev"}:
            continue
        declaration = str(entry["text"]).strip()
        header = declaration_statement_text(declaration)
        if kind in {"def", "abbrev"}:
            body = declaration[len(header) :].lstrip()
            if not body.startswith(":="):
                continue
            # The line index can include closing namespace/section commands.
            body = re.split(
                r"(?m)^\s*(?:end|namespace|section|open|variable|import)\b", body[2:], maxsplit=1
            )[0].strip()
            if _normalized_formula(body) == "True":
                issues.append("predicate definition is tautological (`True`)")
            elif _reflexive_formula(body) or (
                "∧" in body and all(_reflexive_formula(part) for part in body.split("∧"))
            ):
                issues.append("predicate definition is tautological (self-equality)")
            continue
        colon = _top_level_colon(header)
        if colon < 0:
            continue
        target = _normalized_formula(header[colon + 1 :])
        if target == "True":
            issues.append("theorem conclusion is tautological (`True`)")
        if target and target in _binder_hypotheses(header[:colon]):
            issues.append(
                "theorem conclusion is repeated verbatim as a hypothesis (circular target)"
            )
        if _reflexive_formula(target):
            issues.append("theorem conclusion is a tautological self-equality")
    return tuple(dict.fromkeys(issues))
