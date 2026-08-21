# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Scoped source slice: `.leanflow/workflow-state/formalization/HDP-source-full-qa-questions/batches/items-0.1/extracted.txt`
- Gold Lean entry file: `FateXWork/Gold/HDP/Items018353C612/Main.lean`
- Provenance: `manual_gold`; excluded from agent E2E success metrics.
- Status: complete; both source-faithful statements have kernel-checked proofs with no `sorry`.

## Planner Checklist

- [x] Identify definitions and notation that must exist before theorem statements.
- [x] Split large source theorems into Lean-sized lemmas.
- [x] Record source labels/pages/equations for every generated declaration.
- [x] Check local project and Mathlib names before introducing duplicates.
- [x] Verify drafted Lean statements against the source document at planner level.
- [x] Run independent statement/source verification review and apply corrections.
- [x] Attach the complete source proof text when available, or explicitly record why it is unavailable.
- [x] Record a natural-language proof strategy or source proof pointer for each theorem/lemma.
- [x] Resolve all construction stubs before proof handoff.
- [x] Mark the remaining stable `sorry` declaration ready for the prove workflow.

## Import Plan

Direct Lean imports expected in generated Lean files only:
- `Mathlib`

## Suggested Search Modules

Non-gating modules or namespaces to search while proving. Do not force these into `.lean` imports unless the prover actually needs them.
- `Mathlib.Probability.Moments.Variance`
- `Mathlib.Probability.Independence.Integration`
- `Mathlib.Probability.IdentDistrib`
- `Mathlib.MeasureTheory.Function.L2Space`
- `Mathlib.Analysis.InnerProductSpace.PiL2`

## Corpus Reuse Notes

- Corpus blueprint lists item `0.1` first in the execution order and marks candidate concepts `probability`, `expectation`, `variance`, `independence`, `euclidean-space`, and `norm`.
- Typed dependency graph entry for `0.1` has no declared source dependencies.
- Shared modules `FateXWork.Questions.Shared.Probability` and `FateXWork.Questions.Shared.Analysis` are scaffold recommendations only (`auto_import = false`); no verified shared declarations were found for this item.
- Reuse registry search found no existing verified declarations for item `0.1`, variance, expectation, independence, or Euclidean-space formulas.

## Generated File Layout

- Aggregator entry file: `FateXWork/Questions/Items018353C612/Main.lean`
- No split files are used for this one-question batch.
- Parent module `FateXWork/Questions/Items018353C612.lean` imports `FateXWork.Questions.Items018353C612.Main`.
- Root module `FateXWork.lean` imports `FateXWork.Questions.Items018353C612`, so a plain project build covers this target.

## Source Statement Inventory

### 0.1 — KK, Two variance formulas

- Kind: question with two requested identities.
- Source locator: `HDP/source/full/qa/questions.json`, scoped item `[0.1]`; preflight locator `questions.json:pdf-pages-13,14`; extracted cache lines 1-14.
- Planned Lean declarations:
  - Shared definition bridge: `FateXWork.Questions.Shared.RealN`
  - Part (a): `FateXWork.Questions.Items018353C612.vectorVariance_eq_integral_norm_sq_sub_norm_mean_sq`
  - Part (b): `FateXWork.Questions.Items018353C612.vectorVariance_eq_half_integral_norm_sub_independent_copy_sq`
- Dependencies:
  - Direct import: `FateXWork.Questions.Shared.Probability` (which imports the required Mathlib modules).
  - Lean objects: `MeasureTheory.Measure`, `MeasureTheory.IsProbabilityMeasure`, Bochner integral notation `∫`, `MeasureTheory.MemLp`, `EuclideanSpace ℝ (Fin n)`, `ProbabilityTheory.IndepFun`, `ProbabilityTheory.IdentDistrib`.
  - Search-confirmed useful facts for proving: `norm_sub_pow_two_real` / `norm_sub_sq_real`, `integral_inner`, `ProbabilityTheory.variance_def'`, `ProbabilityTheory.IdentDistrib.memLp_snd`, `ProbabilityTheory.IdentDistrib.integral_eq`, and independence integral product lemmas from `Mathlib.Probability.Independence.Integration`.
- Formal statement review:
  - Source 0.1(a) says any random vector `Z` in `ℝ^n` satisfies `E ‖Z - E Z‖₂² = E ‖Z‖₂² - ‖E Z‖₂²`. The Lean theorem represents a random vector as a function `Z : Ω → EuclideanSpace ℝ (Fin n)` on a probability space `(Ω, μ)` and states exactly this equality with Bochner integrals. It adds `MemLp Z 2 μ` to make the second moment and expectation hypotheses explicit.
  - Source 0.1(b) says if `Z'` is an independent copy of `Z`, then `E ‖Z - E Z‖₂² = 1/2 E ‖Z - Z'‖₂²`. The Lean theorem uses `IndepFun Z Z' μ` for independence and `IdentDistrib Z Z' μ μ` for same distribution on the same probability space, and states the same identity using `EuclideanSpace ℝ (Fin n)`.
- Source qualifiers:
  - Mathematical object class: random vectors in finite-dimensional Euclidean space `ℝ^n`.
  - Quantifier order: dimension `n`, probability space, probability measure, then random vector(s) `Z` and `Z'`.
  - Parameter domain: arbitrary probability space; codomain `EuclideanSpace ℝ (Fin n)`.
  - Equality conditions: squared Euclidean norm identities for mean squared deviation, and independent-copy half-pairwise-distance formula.
  - Side conditions implicit in the source: expectations and squared-norm expectations exist; Lean records this as `MemLp Z 2 μ`.
  - Part (b) side conditions: `Z'` is independent of `Z` and has the same distribution as `Z`.
- Lean coverage:
  - `EuclideanSpace ℝ (Fin n)` covers `ℝ^n` with the Euclidean norm and inner product.
  - `[IsProbabilityMeasure μ]` covers the probability-space expectation context.
  - `MemLp Z 2 μ` covers square-integrability/finiteness of the variance expressions; for part (b), `IdentDistrib.memLp_snd` can derive the corresponding `MemLp Z' 2 μ` during proof.
  - `IndepFun Z Z' μ` and `IdentDistrib Z Z' μ μ` cover the independent-copy qualifier.
  - The two Lean theorem conclusions cover the two displayed source equalities without changing the displayed constants or sides.
- Scope changes:
  - Explicitly adds the side condition `MemLp Z 2 μ`, which is mathematically implicit in the source phrase involving finite expectations/variance.
  - Represents random vectors as measurable/probabilistic functions into `EuclideanSpace ℝ (Fin n)` rather than introducing a separate random-vector structure; this is the standard Mathlib representation and no extra construction stub is needed.
  - Uses an arbitrary natural number `n`; no positivity assumption on `n` is added.
- Statement and proof verification status: complete. Both declarations match the scoped source, contain no `sorry`, and `lake build FateXWork.Questions.Shared.Probability FateXWork.Questions.Items018353C612.Main` succeeds.
- Complete source proof:
  - The scoped source includes only the optional reference-solution hint, not a complete separate derivation for both parts. Full available proof text from the source slice: “Recall that `‖x - y‖₂² = ‖x‖₂² - 2⟨x,y⟩ + ‖y‖₂²`. (This follows by expanding `‖x-y‖₂² = ⟨x-y,x-y⟩`.) Use this formula for `‖Z - E Z‖₂²`.”
- Source proof / prover notes:
  - For 0.1(a), expand `‖Z ω - m‖²` with `m = ∫ ω, Z ω ∂μ`, integrate termwise, use linearity of the Bochner integral/inner product, and use `μ univ = 1` from `[IsProbabilityMeasure μ]` to cancel `-2‖m‖² + ‖m‖²` to `-‖m‖²`.
  - For 0.1(b), expand `‖Z ω - Z' ω‖²`, use identical distribution to replace the `Z'` squared-norm expectation and mean by those of `Z`, and use independence to identify the mixed inner-product expectation with `⟪E Z, E Z'⟫`. Then simplify to `E‖Z-Z'‖² = 2 * (E‖Z‖² - ‖E Z‖²)` and invoke part (a).

## Verified Shared-Library Promotion

- `FateXWork.Questions.Shared.RealN` is defined in `Shared/Analysis.lean`.
- `integrable_inner_of_indepFun` and `integral_inner_eq_inner_integral_of_indepFun` are proved in `Shared/Probability.lean` by reducing Euclidean inner products to coordinate sums and applying Mathlib's scalar independent-product integration theorem.
- Item 0.1 imports and consumes these shared declarations; later probability and concentration questions can reuse them without replaying the coordinate proof.
