"""Inventory reusable Lean declarations and preserve promotion evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from leanflow_cli.formalization.corpus_planning import concepts_for_text
from leanflow_cli.lean.lean_parsing import _declaration_line_index_from_text

_REUSABLE_KINDS = {"abbrev", "class", "def", "instance", "structure"}
_NAMESPACE_OPEN_RE = re.compile(r"^\s*namespace\s+([A-Za-z_][A-Za-z0-9_']*)\s*$")
_NAMESPACE_END_RE = re.compile(r"^\s*end(?:\s+([A-Za-z_][A-Za-z0-9_']*))?\s*$")


def _normalized_declaration(text: str) -> str:
    """Return a whitespace-stable declaration identity."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _namespace_at_line(source: str, line: int) -> list[str]:
    """Return the simple namespace stack immediately before a declaration line."""
    stack: list[str] = []
    for raw_line in source.splitlines()[: max(0, line - 1)]:
        opened = _NAMESPACE_OPEN_RE.match(raw_line)
        if opened:
            stack.append(opened.group(1))
            continue
        closed = _NAMESPACE_END_RE.match(raw_line)
        if not closed or not stack:
            continue
        named = str(closed.group(1) or "")
        if named and named in stack:
            while stack and stack[-1] != named:
                stack.pop()
        if stack:
            stack.pop()
    return stack


def _existing_promotions(path: Path) -> list[dict[str, Any]]:
    """Load durable promotion records without trusting malformed state."""
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    promotions = payload.get("promotions", []) if isinstance(payload, Mapping) else []
    return [dict(item) for item in promotions if isinstance(item, Mapping)]


def build_reuse_registry(workspace: Path, *, registry_path: Path) -> dict[str, Any]:
    """Find exact repeated reusable declarations and retain verified promotion records."""
    occurrences: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(workspace.rglob("*.lean")):
        if "Shared" in path.relative_to(workspace).parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for entry in _declaration_line_index_from_text(source):
            kind = str(entry.get("kind", "") or "")
            name = str(entry.get("name", "") or "")
            text = str(entry.get("text", "") or "")
            if kind not in _REUSABLE_KINDS or not name or not text:
                continue
            normalized = _normalized_declaration(text)
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
            occurrences[(kind, name, digest)].append(
                {
                    "file": path.relative_to(workspace).as_posix(),
                    "line": int(entry.get("line", 0) or 0),
                    "has_sorry": bool(entry.get("has_sorry", False)),
                }
            )

    candidates = [
        {
            "kind": kind,
            "name": name,
            "declaration_digest": digest,
            "status": "candidate",
            "consumers": consumers,
            "promotion_eligible": False,
            "required_gate": "two project-verified consumers or an explicit source-level definition",
        }
        for (kind, name, digest), consumers in sorted(occurrences.items())
        if len(consumers) >= 2
    ]
    promotions = _existing_promotions(registry_path)
    return {
        "schema_version": "1",
        "workspace": str(workspace),
        "duplicate_candidates": candidates,
        "promotions": promotions,
        "promotion_contract": {
            "allowed_statuses": ["candidate", "verified", "promoted"],
            "minimum_verified_consumers": 2,
            "requires_project_verification": True,
            "automatic_source_rewrite": False,
        },
    }


def promotion_eligible(record: Mapping[str, Any]) -> bool:
    """Return whether a promotion record carries enough explicit verification evidence."""
    if bool(record.get("explicit_source_definition", False)) and bool(
        record.get("project_verified", False)
    ):
        return True
    consumers = record.get("verified_consumers", []) or []
    unique_consumers = {str(value).strip() for value in consumers if str(value).strip()}
    return len(unique_consumers) >= 2 and bool(record.get("project_verified", False))


def build_placement_report(
    workspace: Path,
    *,
    architecture: Mapping[str, Any],
    reuse_registry: Mapping[str, Any],
) -> dict[str, Any]:
    """Recommend locations for reusable declarations without rewriting source files."""
    concept_modules: dict[str, str] = {}
    for module in architecture.get("modules", []) or []:
        if not isinstance(module, Mapping):
            continue
        module_name = str(module.get("module", "") or "")
        for concept in module.get("routing_concepts", []) or []:
            concept_modules[str(concept)] = module_name
    duplicates = {
        (str(item.get("name", "")), str(item.get("declaration_digest", ""))): item
        for item in reuse_registry.get("duplicate_candidates", []) or []
        if isinstance(item, Mapping)
    }
    verified_promotions = {
        (str(item.get("name", "")), str(item.get("declaration_digest", "")))
        for item in reuse_registry.get("promotions", []) or []
        if isinstance(item, Mapping) and promotion_eligible(item)
    }

    placements: list[dict[str, Any]] = []
    for path in sorted(workspace.rglob("*.lean")):
        relative = path.relative_to(workspace)
        if "Shared" in relative.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for entry in _declaration_line_index_from_text(source):
            kind = str(entry.get("kind", "") or "")
            name = str(entry.get("name", "") or "")
            text = str(entry.get("text", "") or "")
            if kind not in _REUSABLE_KINDS or not name or not text:
                continue
            digest = hashlib.sha256(_normalized_declaration(text).encode("utf-8")).hexdigest()[:16]
            concepts = concepts_for_text(f"{name} {text}")
            targets = list(
                dict.fromkeys(
                    concept_modules[concept] for concept in concepts if concept in concept_modules
                )
            )
            key = (name, digest)
            if key in verified_promotions:
                status = "approved_for_promotion"
            elif key in duplicates:
                status = "promotion_candidate"
            elif targets:
                status = "placement_review"
            else:
                status = "local_default"
            placements.append(
                {
                    "file": relative.as_posix(),
                    "line": int(entry.get("line", 0) or 0),
                    "kind": kind,
                    "name": name,
                    "namespace": _namespace_at_line(source, int(entry.get("line", 0) or 0)),
                    "qualified_name": ".".join(
                        [
                            *_namespace_at_line(source, int(entry.get("line", 0) or 0)),
                            name,
                        ]
                    ),
                    "declaration_digest": digest,
                    "concepts": concepts,
                    "recommended_modules": targets,
                    "status": status,
                    "automatic_move": False,
                    "_source_text": text,
                }
            )
    placements_by_file: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for placement in placements:
        placements_by_file[str(placement["file"])][str(placement["name"])] = placement
    for placement in placements:
        source_text = str(placement.pop("_source_text", "") or "")
        local_dependencies: list[str] = []
        dependency_blockers: list[dict[str, Any]] = []
        for dependency_name, dependency in placements_by_file[str(placement["file"])].items():
            if dependency_name == placement["name"]:
                continue
            if not re.search(
                rf"(?<![A-Za-z0-9_']){re.escape(dependency_name)}(?![A-Za-z0-9_'])", source_text
            ):
                continue
            local_dependencies.append(dependency_name)
            shared_target = bool(
                set(placement["recommended_modules"]).intersection(
                    dependency["recommended_modules"]
                )
            )
            if dependency["status"] != "approved_for_promotion" and not shared_target:
                dependency_blockers.append(
                    {
                        "name": dependency_name,
                        "status": dependency["status"],
                        "recommended_modules": dependency["recommended_modules"],
                    }
                )
        placement["local_dependencies"] = local_dependencies
        placement["dependency_blockers"] = dependency_blockers
        if dependency_blockers and placement["status"] != "local_default":
            placement["pre_block_status"] = placement["status"]
            placement["status"] = "blocked_local_dependency"

    transactions = [
        {
            "name": placement["name"],
            "namespace": placement["namespace"],
            "qualified_name": placement["qualified_name"],
            "declaration_digest": placement["declaration_digest"],
            "source_file": placement["file"],
            "target_modules": placement["recommended_modules"],
            "local_dependencies": placement["local_dependencies"],
            "status": "ready_for_verified_candidate_patch",
        }
        for placement in placements
        if placement["status"] == "approved_for_promotion"
        and not placement["dependency_blockers"]
        and placement["recommended_modules"]
    ]
    return {
        "schema_version": "1",
        "workspace": str(workspace),
        "placements": placements,
        "transaction_candidates": transactions,
        "contract": {
            "automatic_move": False,
            "approved_for_promotion_requires_registry_evidence": True,
            "transactions_require_closed_local_dependencies": True,
            "final_authority": "project_verification",
        },
    }
