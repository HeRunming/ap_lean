# Corpus Blueprint: HDP/source/pilot_questions.json

- Items: 2
- Shared Lean module: `FateXWork.PilotQuestions.Shared.Basic`
- Candidate edges are retrieval hints, not verified Lean dependencies.
- Promote code only after two verified consumers or an explicit source-level definition.

## Execution Plan

- Policy: `declared_dependencies_only_with_source_order_ties`
- Schedulable: `true`
- Order: 1.1, 1.2
- Cycles: [none]

## Library Architecture

- `FateXWork.PilotQuestions.Shared.Convexity`: 2 candidate consumers; concepts convexity [not auto-imported]

## Shared Concept Candidates

- `convexity`: 2 items (1.1, 1.2)

## Typed Dependency Candidates

- `1.2` → `1.1`: shared_foundation [candidate]

## Item Inventory

- `1.1` (chapter 1): euclidean-space, convex-hull, convexity
- `1.2` (chapter 1): convexity, finite-family, pointwise-operations, maximum
