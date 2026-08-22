# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Scoped source slice: `items-0.5`, extracted at `.leanflow/workflow-state/formalization/HDP-source-full-qa-questions/batches/items-0.5/extracted.txt`
- Target Lean entry file: `FateXWork/Questions/Items05784E1F74/Main.lean`
- Status: corrected after an independent BLOCK review; awaiting fresh independent statement/source verification.

## Planner Checklist

- [x] Identify definitions and notation that must exist before theorem statements.
- [x] Split the source claim into a witness-membership theorem, the distance lower bound, and its numerical asymptotic corollary.
- [x] Record source locators, qualifiers, source proof availability, and prover notes.
- [x] Check local project and Mathlib names before introducing the representation.
- [x] Compare the drafted statements with the bounded source slice.
- [x] Apply the prior independent review's corrections: state the positive-dimension scope in one bundled witness theorem and distinguish the numerical limit from the unavailable comparison theorem.
- [ ] Obtain fresh independent statement/source verification review.
- [ ] Mark stable theorem/lemma/example `sorry` declarations ready for a user-started prove workflow. (Only after independent review approves every source entry.)

## Import Plan

Direct Lean imports in `FateXWork/Questions/Items05784E1F74/Main.lean`:

- `FateXWork.Questions.Shared.Analysis`

## Suggested Search Modules

Non-gating modules or namespaces to search while proving. They are not direct imports unless a proof needs one explicitly.

- `Mathlib.Analysis.Convex.Combination`
- `Mathlib.Analysis.InnerProductSpace.PiL2`
- `Mathlib.Topology.Algebra.Order`

## Generated File Layout

- `FateXWork/Questions/Items05784E1F74/Main.lean`: construction witnesses and the three source-backed theorem skeletons.
- `FateXWork/Questions/Items05784E1F74.lean`: existing aggregator importing `Main`.
- `FateXWork.lean`: already imports `FateXWork.Questions.Items05784E1F74`, so the root project build covers this target.

A split is not warranted for this one-item draft. Reconsider organization only after statement/source review.

## Definitions and Representation Bridge

- `standardSimplexVertex i : RealN n` is the standard basis vector `e_i`, implemented by `EuclideanSpace.single i 1`.
- `standardSimplexVertices n : Set (RealN n)` is the range of the preceding vertex map and is the source set `T = {e₁, …, eₙ}`.
- `standardSimplexCenter n : RealN n` is the constant vector with every coordinate `1 / n`; this is the center (barycenter) of that simplex.
- `RealN n` is the project-shared abbreviation for `EuclideanSpace ℝ (Fin n)`. Mathlib's norm on this type is the Euclidean `ℓ₂` norm (`EuclideanSpace.norm_eq`).
- A source sequence `x₁, …, xₖ ∈ T` is represented literally by `points : Fin k → RealN n` together with `∀ j, points j ∈ standardSimplexVertices n`; repetitions are permitted, as in the source wording.

## Source Statement Inventory

### 0.5

#### Approximate Carathéodory is asymptotically tight

- Kind: question.
- Source locator: `HDP/source/full/qa/questions.json`, item `0.5`; bounded extracted slice `[0.5]`, lines 1–8 of `.leanflow/workflow-state/formalization/HDP-source-full-qa-questions/batches/items-0.5/extracted.txt` (preflight identified the question at source line 5).
- Planned Lean declarations:
  - `standardSimplexVertex`
  - `standardSimplexVertices`
  - `standardSimplexCenter`
  - `standardSimplexCenter_mem_convexHull`
  - `standardSimplex_positive_dimension_witness`
  - `standardSimplex_approximateCaratheodory_lower_bound`
  - `standardSimplex_displayed_lower_bound_tendsto`
- Dependencies:
  - `FateXWork.Questions.Shared.RealN` through `FateXWork.Questions.Shared.Analysis`.
  - Mathlib finite convex hulls (`convexHull`) and Euclidean spaces (`EuclideanSpace.single`, `EuclideanSpace.norm_eq`).
  - The lower-bound theorem is independent of the convex-hull-membership theorem at the level of Lean proof dependencies, but together they provide the required witnesses.
  - The numerical-limit theorem depends only on the displayed lower-bound expression.
- Formal statement review:
  - `standardSimplex_positive_dimension_witness` is the direct existential reading of the source construction, with the required `n > 0` convention stated at its outer quantifier.
  - For every positive dimension `n`, the definitions give the exact source witnesses `T ⊆ ℝⁿ` and its center `x`.
  - `standardSimplexCenter_mem_convexHull` formalizes `x ∈ conv(T)`.
  - `standardSimplex_approximateCaratheodory_lower_bound` universally quantifies a positive number `k` of points of `T`, nonnegative real weights summing to one, and concludes the exact displayed inequality with the Euclidean norm and `√(1/k - 1/n)`.
  - `standardSimplex_displayed_lower_bound_tendsto` formalizes the stated fixed-`k`, `n → ∞` limiting calculation, indexing positive dimensions as `n + 1`. It deliberately asserts only this numerical limit, not a comparison with the unavailable Theorem 0.0.2.
- Source qualifiers:
  - Object class: a set in the real Euclidean space `ℝⁿ`; represented by `RealN n = EuclideanSpace ℝ (Fin n)`.
  - Witness set and point: the standard basis vectors and simplex center.
  - Quantifier order: first `n`, then every convex combination of `k` points; `k` is positive because a finite convex combination whose weights sum to one has a nonempty index type.
  - Weights: real, coordinatewise nonnegative, and summing to one.
  - Repetitions: source points are a sequence, so repetitions are allowed by `Fin k → RealN n`.
  - Output: the Euclidean norm lower bound exactly displayed in the source.
  - Follow-on claim: the lower-bound expression approaches `√(1/k)` at fixed positive `k`; the source then uses the unprovided Theorem 0.0.2 to characterize this as asymptotic tightness.
- Lean coverage: partial but source-faithful for the positive-dimension construction, convex-hull membership, universal convex-combination inequality, and the numerical limiting claim. The representation bridge above records why `RealN n` is `ℝⁿ` with the required `ℓ₂` norm. The direct bundled witness theorem makes the quantifier order and the positive-dimension convention explicit.
- Scope changes:
  - The source slice does not state Theorem 0.0.2, so no Lean declaration formally compares this lower bound with its absent upper bound or concludes the external phrase “Theorem 0.0.2 is asymptotically tight.” Accordingly, this entry is explicitly partial coverage: it formalizes the construction and the numerical limit supporting the omitted comparison.
  - The formalization uses the standard positive-natural-dimension convention `n > 0`. This is necessary, not merely cosmetic: in dimension zero `RealN 0` has a single point, so every convex combination equals the center while the displayed right-hand side is positive for positive `k`. Thus the literal `n = 0` case is false under Lean's real division convention.
  - `k > 0` is an explicit Lean side condition, corresponding to the source's nonempty convex-combination language.
- Statement verification status: awaiting independent review.
- Complete source proof: unavailable in the bounded source slice. The supplied optional reference hint is the complete available proof text: “Choose `T = {e₁, …, eₙ}` where `eᵢ` are the standard basis vectors. Then `conv(T)` is an `(n - 1)`-dimensional simplex; let `x` be the center of the simplex. It remains to calculate the distance from `x` to each `(k - 1)`-dimensional face of the simplex.”
- Source proof / prover notes:
  - Prove center membership by the uniform weights `1/n` on every basis vector.
  - For a convex combination, each coordinate is the total weight placed on the corresponding basis vector. The resulting coordinate vector is nonnegative, has coordinate sum one, and has support cardinality at most `k`.
  - Expand the squared Euclidean distance to the uniform vector. Cauchy–Schwarz on at most `k` nonzero coordinates gives squared norm at least `1/k`; hence the squared distance is at least `1/k - 1/n`, then use monotonicity of `Real.sqrt`.
  - For the final limit, use `(n + 1 : ℝ)⁻¹ → 0` and continuity of subtraction and `Real.sqrt` on the nonnegative limiting value.
