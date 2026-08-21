# Formalization Blueprint: HDP/source/pilot_questions.json

- Source: `HDP/source/pilot_questions.json`
- Target Lean entry file: `FateXWork/PilotQuestions/Items1152CE4D1A/Main.lean`
- Status: planner draft prepared; awaiting independent statement/source verification before proof handoff.

## Planner Checklist

- [x] Identify definitions and notation that must exist before theorem statements.
- [x] Split large source theorems into Lean-sized lemmas. (No split needed for item 1.1.)
- [x] Record source labels/pages/equations for every generated declaration.
- [x] Check local project and Mathlib names before introducing duplicates.
- [x] Compare the drafted Lean statement against the source document in this planner pass.
- [x] Run independent statement/source verification review and apply corrections.
- [x] Attach the complete source proof text when available, or explicitly record why it is unavailable.
- [x] Record a natural-language proof strategy or source proof pointer for each theorem/lemma.
- [x] Resolve all construction stubs before proof handoff.
- [ ] Mark stable theorem/lemma/example `sorry` declarations ready for a user-started prove workflow. (Only check after independent reviewer sign-off for every source entry.)

## Import Plan

Direct Lean imports used by generated/project coverage files:
- `FateXWork/PilotQuestions/Items1152CE4D1A/Main.lean`: `Mathlib`
- `FateXWork/PilotQuestions/Items1152CE4D1A.lean`: `FateXWork.PilotQuestions.Items1152CE4D1A.Main`
- `FateXWork.lean`: `FateXWork.PilotQuestions.Items1152CE4D1A`

## Suggested Search Modules

Non-gating modules or namespaces to search while proving. Do not force these into `.lean` imports unless the prover actually needs them.
- `Mathlib.Analysis.Convex.Hull`
- Theorem: `convex_convexHull`
- Existing local analogue: `HDP.Pilot.exercise_1_1` in `HDP/Pilot.lean`

## Generated File Layout

- Source-backed theorem file: `FateXWork/PilotQuestions/Items1152CE4D1A/Main.lean`
- Aggregator entry file: `FateXWork/PilotQuestions/Items1152CE4D1A.lean`
- Project module root: `FateXWork.lean`
- Default Lake target root coverage: `lakefile.lean` declares `FateXWork` as a default Lean library target, and `FateXWork.lean` imports the generated module so plain project verification reaches this draft.
- No split into `Basic.lean`, `Constructions.lean`, or `Theorems.lean` is justified for this one-item batch.

## Local/Mathlib Search Notes

- Mathlib search for "convex hull is convex" returned `convex_convexHull` from `Mathlib.Analysis.Convex.Hull`:
  `∀ (𝕜) (s : Set E), Convex 𝕜 (convexHull 𝕜 s)` under the usual ordered semiring/module hypotheses.
- Local project search found `HDP.Pilot.exercise_1_1`, which formalizes the same source item as
  `Convex ℝ (convexHull ℝ T)` for `T : Set (EuclideanSpace ℝ (Fin n))`.

## Source Statement Inventory

### 1.1

- Kind: question
- Source locator: `HDP/source/pilot_questions.json`, item `1.1`; manifest locator `pilot_questions.json:pdf-pages-28`; visual audit pages `[28]`.
- Source statement: `1.1 K Consider any subset $T \subset \mathbb{R}^n$. Check that $\operatorname{conv}(T)$ is a convex set.`
- Lean namespace: `FateXWork.PilotQuestions.Items1152CE4D1A`
- Planned Lean declarations:
  - `RealN`
  - `convHullInRealN`
  - `item_1_1_convex_convexHull`
- Lean statement:
  ```lean
  theorem item_1_1_convex_convexHull {n : ℕ} (T : Set (RealN n)) :
      Convex ℝ (convHullInRealN T) := by
    sorry
  ```
- Dependencies: `Mathlib`; Mathlib notions `EuclideanSpace`, `Set`, `Convex`, `convexHull`, and theorem `convex_convexHull`.
- Formal statement review: The source quantifies over an arbitrary dimension `n` and an arbitrary subset `T` of `ℝ^n`, then asserts convexity of `conv(T)`. The Lean theorem quantifies over arbitrary `n : ℕ` and arbitrary `T : Set (RealN n)`, where `RealN n` is the recorded Mathlib representation of `ℝ^n`, and asserts `Convex ℝ (convHullInRealN T)`, where `convHullInRealN` unfolds to `convexHull ℝ T`. No side condition appears in the source or Lean statement.
- Source qualifiers:
  - Mathematical object class: arbitrary finite-dimensional real coordinate space `ℝ^n` and arbitrary subset `T`.
  - Quantifier order: for every natural dimension `n`, for every subset `T` of that space.
  - Parameter domain: `T ⊆ ℝ^n`, represented by `T : Set (RealN n)`.
  - Output codomain/equality/image condition: the convex hull `conv(T)` is a subset of the same ambient real vector space and is convex over `ℝ`.
  - Side conditions: none.
  - Follow-on claims: none.
- Lean coverage:
  - `RealN n` records the source ambient space `ℝ^n` as `EuclideanSpace ℝ (Fin n)`.
  - `convHullInRealN T` records the source notation `conv(T)` as `convexHull ℝ T`.
  - `item_1_1_convex_convexHull` states `Convex ℝ (convHullInRealN T)` for every `n` and every `T`.
- Scope changes: none. The representation choice is the standard Mathlib model `EuclideanSpace ℝ (Fin n)` for `ℝ^n`, recorded by `RealN`; the source does not specify a different coordinate representation.
- Statement verification status: approved by codex verifier
- Complete source proof: unavailable. The JSON fields `answer` and `solution` for item `1.1` are empty, and preflight detected no source proof block.
- Source proof / prover notes: The intended proof is immediate from the definition/theorem that a convex hull is convex. In Mathlib, unfold `convHullInRealN` and apply `convex_convexHull ℝ T`; a likely proof is `simpa [convHullInRealN] using convex_convexHull ℝ T` after the independent statement review clears the skeleton.
- Source proof excerpt: none detected by preflight.
