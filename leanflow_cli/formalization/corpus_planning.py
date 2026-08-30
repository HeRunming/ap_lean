"""Build deterministic corpus-level plans for document formalization."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_CONCEPT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("euclidean-space", r"(?:\\mathbb\{R\}|ℝ)\s*\^\s*\{?n\}?|euclidean\s*space"),
    ("convex-hull", r"convex\s*hull|\\operatorname\{conv\}|\bconv\s*\("),
    ("convexity", r"\bconvex(?:ity)?\b"),
    ("finite-family", r"finite (?:number|family|set|collection)"),
    ("pointwise-operations", r"pointwise|point-wise"),
    ("maximum", r"\bmaximum\b|\bmax\b|\\max"),
    ("probability", r"probabil|random variable|random vector"),
    ("expectation", r"expectation|expected value|\\mathbb\{E\}"),
    ("variance", r"variance|\\operatorname\{Var\}"),
    ("norm", r"\bnorm\b|\\lVert|‖"),
    ("matrix", r"\bmatrix|matrices\b"),
    ("independence", r"\bindependent|independence\b"),
)
_OPERATOR_CONCEPT_ALIASES = {
    "conv": "convex-hull",
    "var": "variance",
    "diam": "diameter",
}
_DOMAIN_CONCEPTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Convexity", ("convexity", "convex-hull", "pointwise-operations")),
    ("Probability", ("probability", "expectation", "variance", "independence")),
    ("LinearAlgebra", ("matrix",)),
    ("Analysis", ("euclidean-space", "norm", "maximum")),
)


def source_formalization_complexity(text: str) -> dict[str, int | str]:
    """Return a conservative source and formalization-cost hint, never a verdict."""
    source = str(text or "")
    subpart_count = len(re.findall(r"(?m)^\s*\([a-z0-9]+\)\s+", source))
    display_count = len(re.findall(r"\$\$", source)) // 2
    meta_proof_repair = bool(
        re.search(
            r"\b(?:fix|correct|repair|tighten|improve)\b.{0,80}\b(?:proof|argument)\b|"
            r"\b(?:proof|argument)\b.{0,80}\b(?:flawed|incorrect|wrong|gap)\b|"
            r"\bwhat (?:is|goes) wrong\b",
            source,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )
    semantic_depth_score = sum(
        weight
        for pattern, weight in (
            (
                r"\b(?:random variables?|random vectors?|random matrix|probability|"
                r"expectation|expected value|variance|independen(?:t|ce)|distributions?|"
                r"probabilistic)\b|\\mathbb\{[PE]\}",
                8,
            ),
            (
                r"\b(?:matrix|matrices|parseval frame|orthonormal rows?|orthonormal columns?|random matrix|"
                r"singular values?|eigenvalues?|positive semidefinite|spectral norm|"
                r"orthogonal projections?|operator norm|hilbert space)\b",
                8,
            ),
            (
                r"\b(?:VC dimension|VC dichotomy|growth function|shatter(?:ed|ing)?|"
                r"sauer(?:-shelah)? lemma)\b|\\Pi_\{?\\mathcal",
                8,
            ),
            (
                r"\b(?:covering number|packing number|metric entropy)\b|"
                r"\\varepsilon\$?\s*[- ]?net\b|[εϵ]\$?\s*[- ]?net\b",
                8,
            ),
            (
                r"\b(?:gaussian width|effective dimension|support function|sparse recovery|"
                r"sparse vectors?|dudley integral|gamma[_ ]?2 functional)\b|"
                r"\\gamma_2|\\asymp",
                8,
            ),
            (
                r"\b(?:hoeffding|subgaussian|moment[- ]generating|\bmgf\b|"
                r"concentration inequality|tail bound)\b",
                8,
            ),
            (
                r"\b(?:asymptotically tight|demonstrate by example|construct an? |"
                r"find (?:a|the) (?:set|family|example)|high dimensions?)\b",
                8,
            ),
            (
                r"\b(?:properties?|parts?)\s*\([ivxa-z0-9]+\).{0,60}"
                r"\b(?:Proposition|Theorem|Lemma)\s+\d|"
                r"\bequivalence of properties\b",
                8,
            ),
            (
                r"\b(?:Proposition|Theorem|Lemma|Definition|Corollary|Exercise|Example|Remark|"
                r"Section)\s+\$?\d+(?:\.\d+)+\$?",
                8,
            ),
            (
                r"\b(?:find|construct|give an? example of)\b.{0,100}"
                r"\b(?:random variables?|random vectors?|distributions?)\b|"
                r"\b(?:uncorrelated|dependent)\b.{0,80}\b(?:normal|random variables?)\b",
                8,
            ),
            (
                r"\b(?:limit|derivative|differentiab|integral|measurab|integrab|"
                r"almost everywhere|essential supremum)\b|\b(?:limsup|liminf)\b",
                3,
            ),
            (
                r"\bfor every\b.{0,180}\b(?:for any|there exists|find)\b",
                3,
            ),
        )
        if re.search(pattern, source, flags=re.IGNORECASE | re.DOTALL)
    )
    score = (
        subpart_count * 3
        + min(4, len(source) // 500)
        + min(4, display_count)
        + (8 if meta_proof_repair else 0)
        + semantic_depth_score
    )
    tier = (
        "complex"
        if score >= 8 or semantic_depth_score >= 6
        else "moderate" if score >= 4 else "routine"
    )
    return {
        "source_complexity_score": score,
        "source_complexity_tier": tier,
        "source_subpart_count": subpart_count,
        "source_display_count": display_count,
        "source_meta_proof_repair": int(meta_proof_repair),
        "source_semantic_depth_score": semantic_depth_score,
    }


def _chapter_for_label(label: str) -> str:
    """Return a stable chapter identifier derived from a source label."""
    return str(label or "").partition(".")[0].strip() or "unscoped"


def concepts_for_text(text: str) -> list[str]:
    """Extract conservative reusable mathematical concepts from arbitrary text."""
    concepts = [
        name for name, pattern in _CONCEPT_PATTERNS if re.search(pattern, text, flags=re.IGNORECASE)
    ]
    for command in re.findall(r"\\operatorname\{([^{}]+)\}", text):
        normalized = re.sub(r"[^a-z0-9]+", "-", command.lower()).strip("-")
        normalized = _OPERATOR_CONCEPT_ALIASES.get(normalized, normalized)
        if normalized and normalized not in concepts:
            concepts.append(normalized)
    return concepts


def _concepts_for_block(block: Mapping[str, Any]) -> list[str]:
    """Extract conservative reusable mathematical concepts from one source block."""
    text = " ".join(str(block.get(key, "") or "") for key in ("title", "statement", "proof"))
    return concepts_for_text(text)


def _dependency_schedule(
    items: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> dict[str, Any]:
    """Topologically order source-declared dependencies with stable source-order ties."""
    labels = [str(item["label"]) for item in items]
    ordinals = {str(item["label"]): int(item["ordinal"]) for item in items}
    dependencies: dict[str, set[str]] = {label: set() for label in labels}
    unresolved: list[dict[str, str]] = []
    for edge in edges:
        if edge.get("status") != "declared_unverified":
            continue
        source = str(edge.get("from", ""))
        target = str(edge.get("to", ""))
        if source not in dependencies:
            continue
        if target not in dependencies:
            unresolved.append({"item": source, "dependency": target})
            continue
        dependencies[source].add(target)

    remaining = {label: set(values) for label, values in dependencies.items()}
    order: list[str] = []
    while remaining:
        ready = sorted(
            (label for label, values in remaining.items() if not values),
            key=lambda label: ordinals[label],
        )
        if not ready:
            break
        # Select one source-priority node at a time.  Removing every currently
        # ready node as one giant layer can strand a newly-unlocked, early
        # foundation behind hundreds of unrelated later exercises.
        selected = ready[0]
        order.append(selected)
        remaining.pop(selected)
        for values in remaining.values():
            values.discard(selected)

    cycle_labels = sorted(remaining, key=lambda label: ordinals[label])
    order.extend(cycle_labels)
    return {
        "policy": "declared_dependencies_only_with_source_order_ties",
        "order": order,
        "unresolved_dependencies": unresolved,
        "cycle_labels": cycle_labels,
        "schedulable": not unresolved and not cycle_labels,
    }


def _library_architecture(items: list[dict[str, Any]], *, shared_module: str) -> dict[str, Any]:
    """Group recurring concepts into conservative domain-level Lean modules."""
    shared_prefix = shared_module.rsplit(".", 1)[0]
    modules: list[dict[str, Any]] = []
    assigned_concepts: set[str] = set()
    concept_counts = Counter(concept for item in items for concept in item["concepts"])
    for domain, domain_concepts in _DOMAIN_CONCEPTS:
        active_concepts = {concept for concept in domain_concepts if concept_counts[concept] >= 2}
        labels = [
            str(item["label"])
            for item in items
            if set(item["concepts"]).intersection(active_concepts)
        ]
        concepts = sorted(
            {
                concept
                for item in items
                for concept in item["concepts"]
                if concept in active_concepts
            }
        )
        if len(set(labels)) < 2:
            continue
        modules.append(
            {
                "domain": domain,
                "module": f"{shared_prefix}.{domain}",
                "concepts": concepts,
                "routing_concepts": list(domain_concepts),
                "labels": list(dict.fromkeys(labels)),
                "consumer_count": len(set(labels)),
                "status": "scaffold",
                "auto_import": False,
            }
        )
    recurring_unassigned = sorted(
        {
            concept
            for item in items
            for concept in item["concepts"]
            if sum(concept in candidate["concepts"] for candidate in items) >= 2
            and concept not in assigned_concepts
        }
    )
    return {
        "schema_version": "1",
        "basic_module": shared_module,
        "modules": modules,
        "unassigned_recurring_concepts": recurring_unassigned,
        "contract": {
            "scaffolds_are_not_auto_imported": True,
            "imports_require_verified_declarations": True,
            "cross_domain_dependencies_require_project_verification": True,
        },
    }


def build_corpus_plan(
    metadata: Mapping[str, Any], *, source_relative: str, shared_module: str
) -> dict[str, Any]:
    """Build a typed, non-authoritative dependency and reuse plan for a whole corpus."""
    items: list[dict[str, Any]] = []
    known_labels: set[str] = set()
    foundations: list[dict[str, Any]] = []
    for index, raw_foundation in enumerate(metadata.get("source_foundations", []) or []):
        if not isinstance(raw_foundation, Mapping):
            continue
        label = str(raw_foundation.get("label", "") or "").strip()
        source_file = str(raw_foundation.get("source_file", "") or "").strip()
        if not label or not source_file or label in known_labels:
            continue
        consumers = raw_foundation.get("consumers", []) or []
        if isinstance(consumers, str):
            consumers = [consumers]
        dependencies = raw_foundation.get("dependencies", []) or []
        if isinstance(dependencies, str):
            dependencies = [dependencies]
        foundation = {
            "label": label,
            "ordinal": index - len(metadata.get("source_foundations", []) or []),
            "chapter": str(raw_foundation.get("chapter", "foundations") or "foundations"),
            "kind": "source_foundation",
            "source_locator": str(raw_foundation.get("source_locator", "") or ""),
            "source_file": source_file,
            "target_module": str(raw_foundation.get("target_module", "") or ""),
            "concepts": concepts_for_text(str(raw_foundation.get("statement", "") or "")),
            "declared_dependencies": [
                str(value).strip() for value in dependencies if str(value).strip()
            ],
            "consumers": [str(value).strip() for value in consumers if str(value).strip()],
        }
        foundations.append(foundation)
        items.append(foundation)
        known_labels.add(label)
    for ordinal, raw_block in enumerate(metadata.get("theorem_blocks", []) or [], start=1):
        if not isinstance(raw_block, Mapping):
            continue
        label = str(raw_block.get("label", "") or f"item-{ordinal}")
        known_labels.add(label)
        uses = raw_block.get("uses", []) or []
        if isinstance(uses, str):
            uses = [uses]
        source_text = " ".join(
            str(raw_block.get(key, "") or "") for key in ("title", "statement", "proof")
        )
        items.append(
            {
                "label": label,
                "ordinal": ordinal,
                "chapter": _chapter_for_label(label),
                "kind": str(raw_block.get("kind", "question") or "question"),
                "source_locator": str(raw_block.get("source_locator", "") or ""),
                "concepts": _concepts_for_block(raw_block),
                "declared_dependencies": [
                    str(value).strip() for value in uses if str(value).strip()
                ],
                **source_formalization_complexity(source_text),
            }
        )

    concept_counts = Counter(concept for item in items for concept in item["concepts"])
    shared_concepts = sorted(
        (concept for concept, count in concept_counts.items() if count >= 2),
        key=lambda concept: (-concept_counts[concept], concept),
    )
    edges: list[dict[str, Any]] = []
    for foundation in foundations:
        for consumer in foundation["consumers"]:
            edges.append(
                {
                    "from": consumer,
                    "to": foundation["label"],
                    "kind": "uses_source_foundation",
                    "status": "declared_unverified",
                    "target_known": True,
                    "evidence": foundation["source_locator"] or "source foundation sidecar",
                }
            )
    for item in items:
        for dependency in item["declared_dependencies"]:
            edges.append(
                {
                    "from": item["label"],
                    "to": dependency,
                    "kind": "uses_theorem",
                    "status": "declared_unverified",
                    "target_known": dependency in known_labels,
                    "evidence": "source dependencies/uses field",
                }
            )

    informative_limit = max(8, len(items) // 10)
    # Use same-chapter, reasonably selective concepts. These edges guide retrieval only.
    for index, item in enumerate(items):
        candidates: list[tuple[int, dict[str, Any], list[str]]] = []
        current_concepts = set(item["concepts"])
        for earlier in items[:index]:
            if earlier["chapter"] != item["chapter"]:
                continue
            shared = sorted(
                concept
                for concept in current_concepts.intersection(earlier["concepts"])
                if concept_counts[concept] <= informative_limit
            )
            if shared:
                candidates.append((len(shared), earlier, shared))
        for _, earlier, shared in sorted(
            candidates, key=lambda value: (-value[0], -int(value[1]["ordinal"]))
        )[:3]:
            edges.append(
                {
                    "from": item["label"],
                    "to": earlier["label"],
                    "kind": "shared_foundation",
                    "status": "candidate",
                    "target_known": True,
                    "concepts": shared,
                    "evidence": "shared corpus concepts; requires planner and Lean verification",
                }
            )

    execution_plan = _dependency_schedule(items, edges)
    # Surface scheduling signals without treating inferred edges as proof
    # dependencies.  Consumers can use these metrics to prioritize shared
    # foundations while the Lean checker remains authoritative.
    downstream = Counter(str(edge.get("to", "")) for edge in edges if edge.get("status") == "declared_unverified")
    indegree = Counter(str(edge.get("from", "")) for edge in edges if edge.get("status") == "declared_unverified")
    for item in items:
        label = str(item["label"])
        item["dag_metrics"] = {
            "declared_downstream_consumers": int(downstream.get(label, 0)),
            "declared_prerequisite_count": int(indegree.get(label, 0)),
            "reuse_priority": int(downstream.get(label, 0)) * 2 + len(item.get("concepts", [])),
        }
    library_architecture = _library_architecture(items, shared_module=shared_module)
    return {
        "schema_version": "1",
        "source": source_relative,
        "item_count": len(items) - len(foundations),
        "foundation_count": len(foundations),
        "source_foundations": foundations,
        "source_batches": [
            {
                "id": "foundation-" + re.sub(r"[^A-Za-z0-9._-]+", "-", item["label"]),
                "chapter": item["chapter"],
                "selection_kind": "document",
                "source_file": item["source_file"],
                "labels": [item["label"]],
            }
            for item in foundations
        ]
        + [
            dict(batch)
            for batch in metadata.get("qa_batches", []) or []
            if isinstance(batch, Mapping)
        ],
        "items": items,
        "concept_index": [
            {
                "concept": concept,
                "item_count": concept_counts[concept],
                "labels": [item["label"] for item in items if concept in item["concepts"]],
                "reuse_status": "shared_candidate" if concept in shared_concepts else "local",
            }
            for concept in sorted(concept_counts, key=lambda value: (-concept_counts[value], value))
        ],
        "dependency_edges": edges,
        "execution_plan": execution_plan,
        "library_architecture": library_architecture,
        "dependency_policy": {
            "candidate_scope": "same_chapter",
            "maximum_concept_frequency": informative_limit,
            "maximum_candidate_predecessors_per_item": 3,
        },
        "shared_library": {
            "module": shared_module,
            "promotion_rule": "promote only after two verified consumers or an explicit source-level definition",
            "candidate_concepts": shared_concepts,
        },
    }


def selected_corpus_context(plan: Mapping[str, Any], labels: set[str]) -> dict[str, Any]:
    """Project the whole-corpus plan onto selected items and immediate dependencies."""
    architecture = dict(plan.get("library_architecture", {}) or {})
    selected_items = [
        dict(item)
        for item in plan.get("items", []) or []
        if isinstance(item, Mapping) and str(item.get("label", "")) in labels
    ]
    selected_concepts = {
        str(concept) for item in selected_items for concept in item.get("concepts", []) or []
    }
    return {
        "selected_items": selected_items,
        "incoming_dependencies": [
            dict(edge)
            for edge in plan.get("dependency_edges", []) or []
            if isinstance(edge, Mapping) and str(edge.get("from", "")) in labels
        ],
        "execution_positions": {
            label: index + 1
            for index, label in enumerate(
                dict(plan.get("execution_plan", {}) or {}).get("order", []) or []
            )
            if label in labels
        },
        "recommended_shared_modules": [
            {key: value for key, value in dict(module).items() if key not in {"labels"}}
            for module in architecture.get("modules", []) or []
            if isinstance(module, Mapping)
            and labels.intersection(str(value) for value in module.get("labels", []) or [])
        ],
        "shared_library": {
            **dict(plan.get("shared_library", {}) or {}),
            "candidate_concepts": sorted(
                selected_concepts.intersection(
                    str(value)
                    for value in dict(plan.get("shared_library", {}) or {}).get(
                        "candidate_concepts", []
                    )
                )
            ),
        },
    }


def render_corpus_blueprint(plan: Mapping[str, Any]) -> str:
    """Render a compact human-readable book-level planning artifact."""
    shared = dict(plan.get("shared_library", {}) or {})
    lines = [
        f"# Corpus Blueprint: {plan.get('source', '')}",
        "",
        f"- Items: {plan.get('item_count', 0)}",
        f"- Shared Lean module: `{shared.get('module', '')}`",
        "- Candidate edges are retrieval hints, not verified Lean dependencies.",
        "- Promote code only after two verified consumers or an explicit source-level definition.",
        "",
    ]
    execution = dict(plan.get("execution_plan", {}) or {})
    order = [str(label) for label in execution.get("order", []) or []]
    order_preview = ", ".join(order[:20])
    if len(order) > 20:
        order_preview += f", … ({len(order)} items total; see book-manifest.json for full order)"
    lines.extend(
        [
            "## Execution Plan",
            "",
            f"- Policy: `{execution.get('policy', '')}`",
            f"- Schedulable: `{str(bool(execution.get('schedulable', False))).lower()}`",
            f"- Order: {order_preview or '[none]'}",
            f"- Cycles: {', '.join(execution.get('cycle_labels', []) or []) or '[none]'}",
            "",
        ]
    )
    architecture = dict(plan.get("library_architecture", {}) or {})
    lines.extend(["## Library Architecture", ""])
    modules = [
        module for module in architecture.get("modules", []) or [] if isinstance(module, Mapping)
    ]
    lines.extend(
        [
            f"- `{module.get('module', '')}`: {module.get('consumer_count', 0)} candidate consumers; concepts {', '.join(module.get('concepts', []) or [])} [not auto-imported]"
            for module in modules
        ]
        or ["- [no domain modules justified yet]"]
    )
    lines.extend(["", "## Shared Concept Candidates", ""])
    shared_concepts = [
        item
        for item in plan.get("concept_index", []) or []
        if isinstance(item, Mapping) and item.get("reuse_status") == "shared_candidate"
    ]
    lines.extend(
        [
            f"- `{item.get('concept', '')}`: {item.get('item_count', 0)} items ({', '.join(item.get('labels', []) or [])})"
            for item in shared_concepts
        ]
        or ["- [none detected yet]"]
    )
    lines.extend(["", "## Typed Dependency Candidates", ""])
    edges = list(plan.get("dependency_edges", []) or [])
    rendered_edges = [edge for edge in edges if isinstance(edge, Mapping)][:200]
    lines.extend(
        [
            f"- `{edge.get('from', '')}` → `{edge.get('to', '')}`: {edge.get('kind', '')} [{edge.get('status', '')}]"
            for edge in rendered_edges
        ]
        or ["- [none detected yet]"]
    )
    if len(edges) > len(rendered_edges):
        lines.append(
            f"- ... {len(edges) - len(rendered_edges)} additional edges are in dependency-graph.json"
        )
    lines.extend(["", "## Item Inventory", ""])
    for item in plan.get("items", []) or []:
        if isinstance(item, Mapping):
            concepts = ", ".join(item.get("concepts", []) or []) or "[none]"
            lines.append(
                f"- `{item.get('label', '')}` (chapter {item.get('chapter', '')}): {concepts}"
            )
    return "\n".join(lines).rstrip() + "\n"


def corpus_artifact_paths(workspace: Path) -> dict[str, Path]:
    """Return canonical paths for durable corpus planning artifacts."""
    return {
        "blueprint": workspace / "BookBlueprint.md",
        "manifest": workspace / "book-manifest.json",
        "dependency_graph": workspace / "dependency-graph.json",
        "reuse_registry": workspace / "reuse-registry.json",
        "library_architecture": workspace / "library-architecture.json",
        "declaration_placement": workspace / "declaration-placement.json",
        "campaign": workspace / "campaign.json",
    }
