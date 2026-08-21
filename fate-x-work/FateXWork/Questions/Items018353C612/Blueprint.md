# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Scoped item: `items-0.1`, source label `[0.1]`
- Target Lean entry file: `FateXWork/Questions/Items018353C612/Main.lean`
- Planner status: source-backed declaration draft prepared; waiting for independent statement/source verification before proof handoff.

## Planner Checklist

- [x] Identify definitions and notation that must exist before theorem statements.
- [x] Split large source theorems into Lean-sized lemmas.
- [x] Record source labels/pages/equations for every generated declaration.
- [x] Check local project and Mathlib names before introducing duplicates.
- [x] Verify drafted Lean statements match the source document at planner level.
- [x] Run independent statement/source verification review and apply corrections.
- [x] Attach the complete source proof text when available, or explicitly record why it is unavailable.
- [x] Record a natural-language proof strategy or source proof pointer for each theorem/lemma.
- [x] Resolve all construction stubs before proof handoff.
- [ ] Mark stable theorem/lemma/example `sorry` declarations ready for a user-started prove workflow. (Only check after independent review stamps every source entry.)

## Import Plan

Direct Lean imports expected in generated Lean files only:
- `FateXWork.Questions.Shared.Probability`

Root project coverage:
- `FateXWork/Questions/Items018353C612.lean` imports `FateXWork.Questions.Items018353C612.Main`.
- `FateXWork.lean` imports `FateXWork.Questions.Items018353C612`, so a plain project build covers this target module.

## Suggested Search Modules

Non-gating modules or namespaces to search while proving. Do not force these into `.lean` imports unless the prover actually needs them.
- `Mathlib.Probability.IdentDistrib`
- `Mathlib.Probability.Independence.Basic`
- `Mathlib.MeasureTheory.Integral.Bochner.Basic`
- `Mathlib.Analysis.InnerProductSpace.PiL2`
- Shared interfaces already imported through `FateXWork.Questions.Shared.Probability`: `RealN`, `integrable_inner_of_indepFun`, `integral_inner_eq_inner_integral_of_indepFun`.

## Generated File Layout

- Aggregator entry file: `FateXWork/Questions/Items018353C612/Main.lean`
- No split files are planned for this one-question item. The declarations are short and remain together in `Main.lean`.

## Final Sweep Verification Notes

- Target imports are aligned with the direct import plan: `Main.lean` imports only `FateXWork.Questions.Shared.Probability`.
- Root coverage is in place: `FateXWork/Questions/Items018353C612.lean` imports the generated main module, and `FateXWork.lean` imports `FateXWork.Questions.Items018353C612`.
- Current managed continuation reran `lean_inspect` for `FateXWork/Questions/Items018353C612/Main.lean`; the only target diagnostics are the three intended theorem/lemma `sorry` warnings.
- Current managed continuation reran `lean_verify(mode=file_exact)` for `FateXWork/Questions/Items018353C612/Main.lean`; it passed with the three intended theorem/lemma `sorry` warnings.
- Current managed continuation reran `lean_verify(mode=project)` after root imports were confirmed in place; `lake build FateXWork` completed successfully, replaying the generated module with only the three intended theorem/lemma `sorry` warnings.
- Statement/source verification remains awaiting independent review; this draft has not been self-approved.

## Local/Mathlib Search Notes

- `ProbabilityTheory.IdentDistrib` formalizes the source phrase "same distribution".
- `ProbabilityTheory.IdentDistrib.integral_eq` is likely useful for transferring Bochner expectations under identical distribution; current `lean_search` reconfirmed this theorem in `Mathlib.Probability.IdentDistrib`.
- `IndepFun` is the local/Mathlib predicate used by the shared lemmas `integrable_inner_of_indepFun` and `integral_inner_eq_inner_integral_of_indepFun`; current search also found Mathlib independence-integration lemmas such as `ProbabilityTheory.IndepFun.integral_mul'`, but the planned proof should first try the imported shared inner-product helper.
- `FateXWork.Questions.Shared.RealN n` is the project bridge for the book's `\mathbb{R}^n`, implemented as `EuclideanSpace ℝ (Fin n)`.
- Current `lean_outline` of `FateXWork/Questions/Shared/Probability.lean` (interface only, no proof bodies read) lists the two shared theorem interfaces `integrable_inner_of_indepFun` and `integral_inner_eq_inner_integral_of_indepFun`.
- A current broad local project search for the embedded shared interfaces returned no additional project-rg results under the degraded local search surface; no redraft is indicated because the Corpus-Level Reuse Plan supplies the verified shared interfaces and `Main.lean` compiles against the direct import.

## Source Statement Inventory

### 0.1

- Source label: `0.1`
- Title: KK (Two variance formulas)
- Kind: question with two requested identities.
- Source locator: `HDP/source/full/qa/questions.json`, scoped extracted cache `items-0.1`, lines 1-13; preflight source label `[0.1]`.
- Source statement:
  - (a) Any random vector `Z` in `\mathbb{R}^n` satisfies
    `𝔼 ‖Z - 𝔼 Z‖₂² = 𝔼 ‖Z‖₂² - ‖𝔼 Z‖₂²`.
  - (b) If `Z'` is an independent copy of `Z`, then
    `𝔼 ‖Z - 𝔼 Z‖₂² = (1/2) 𝔼 ‖Z - Z'‖₂²`.
- Planned Lean declarations:
  ```lean
  lemma norm_sub_sq_realN {n : ℕ} (x y : RealN n) :
      ‖x - y‖ ^ 2 = ‖x‖ ^ 2 - 2 * inner ℝ x y + ‖y‖ ^ 2

  theorem variance_identity_realN
      {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω} [IsProbabilityMeasure μ]
      {n : ℕ} (Z : Ω → RealN n)
      (hZ : Integrable Z μ)
      (hZ_sq : Integrable (fun ω => ‖Z ω‖ ^ 2) μ) :
      (∫ ω, ‖Z ω - ∫ η, Z η ∂μ‖ ^ 2 ∂μ)
        = (∫ ω, ‖Z ω‖ ^ 2 ∂μ) - ‖∫ η, Z η ∂μ‖ ^ 2

  theorem variance_identity_independent_copy_realN
      {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω} [IsProbabilityMeasure μ]
      {n : ℕ} (Z Zcopy : Ω → RealN n)
      (hZ : Integrable Z μ) (hZcopy : Integrable Zcopy μ)
      (hZ_sq : Integrable (fun ω => ‖Z ω‖ ^ 2) μ)
      (hZcopy_sq : Integrable (fun ω => ‖Zcopy ω‖ ^ 2) μ)
      (h_indep : IndepFun Z Zcopy μ)
      (h_ident : IdentDistrib Z Zcopy μ μ) :
      (∫ ω, ‖Z ω - ∫ η, Z η ∂μ‖ ^ 2 ∂μ)
        = (1 / 2 : ℝ) * ∫ ω, ‖Z ω - Zcopy ω‖ ^ 2 ∂μ
  ```
- Dependencies:
  - `RealN` for the `\mathbb{R}^n` representation.
  - `Measure`, `IsProbabilityMeasure`, Bochner integral notation, and `Integrable` for expectations.
  - `IndepFun` and `IdentDistrib` for "independent copy".
  - Shared proof helpers `integrable_inner_of_indepFun` and `integral_inner_eq_inner_integral_of_indepFun` are expected to be useful for the eventual proof of part (b), but are not statement dependencies beyond the direct import.
- Formal statement review:
  - Part (a) is represented as a theorem over an explicit probability space `(Ω, μ)` and an arbitrary dimension `n : ℕ`, with `Z : Ω → RealN n`. The Lean equality is an equality of real Bochner integrals and the Euclidean norm squared, matching the source expectation identity.
  - Part (b) is represented on the same probability space by `Zcopy : Ω → RealN n`; the source's "independent copy" is covered by two hypotheses: `IndepFun Z Zcopy μ` for independence and `IdentDistrib Z Zcopy μ μ` for identical distribution.
  - The deterministic inner-product expansion used in the reference solution is split as `norm_sub_sq_realN`.
- Source qualifiers:
  - Mathematical object class: random vectors in `\mathbb{R}^n`; Lean uses functions into `RealN n` on a measurable probability space.
  - Quantifier order: arbitrary probability space, arbitrary `n`, arbitrary random vector `Z`; for part (b), arbitrary second random vector `Zcopy` with independent-copy hypotheses.
  - Parameter domain/codomain: `Ω → RealN n`, where `RealN n = EuclideanSpace ℝ (Fin n)`.
  - Equality conditions: the two displayed real expectation identities are formalized directly with Bochner integrals.
  - Side conditions: the source omits measurability/integrability assumptions; Lean states `Integrable Z μ` and square-integrability of the displayed real-valued squared-norm functions. Part (b) also states `Integrable Zcopy μ` and square-integrability for `Zcopy`.
  - Follow-on claims: none beyond the two displayed identities.
- Lean coverage:
  - `norm_sub_sq_realN` covers the source proof's Euclidean expansion `‖x - y‖₂² = ‖x‖₂² - 2⟪x,y⟫ + ‖y‖₂²`.
  - `variance_identity_realN` covers source part (a).
  - `variance_identity_independent_copy_realN` covers source part (b), with `Zcopy` as the Lean name for source `Z'`.
- Scope changes:
  - Explicit probability-space parameter `μ` and `IsProbabilityMeasure μ` are added because Lean does not have an ambient `𝔼` notation in the source context.
  - Measurability/integrability is made explicit through `Integrable` and squared-norm integrability hypotheses; these are mathematical side conditions implicit in finite second-moment variance formulas.
  - Representation bridge: the source's `\mathbb{R}^n` is represented by the shared project abbreviation `RealN n = EuclideanSpace ℝ (Fin n)`.
  - The theorem is not specialized to a concrete distribution; it remains as general as the source statement.
- Statement verification status: approved by codex-standalone-recovery verifier
- Complete source proof text available in the scoped source slice:
  > Reference solution (optional hint): 0.1 Recall that `‖ x - y ‖₂² = ‖ x ‖₂² - 2 ⟪x, y⟫ + ‖ y ‖₂²`. (This follows by expanding `‖x - y‖₂² = ⟪x - y, x - y⟫`.) Use this formula for `‖Z - 𝔼 Z‖₂²`.
  The scoped slice provides this proof hint only; no longer complete solution text is available in the bounded source excerpt.
- Source proof / prover notes:
  - For `norm_sub_sq_realN`, expand `‖x - y‖²` through `inner ℝ (x - y) (x - y)`, then use bilinearity/symmetry of the real inner product and `norm_sq_eq_inner`-style lemmas.
  - For `variance_identity_realN`, apply `norm_sub_sq_realN` pointwise with `y = ∫ η, Z η ∂μ`, integrate the resulting identity, use integral linearity, `∫ ω, Z ω ∂μ` for the mean, and the probability-measure fact that the integral of a constant is the constant. The mixed term becomes `inner ℝ (∫ ω, Z ω ∂μ) (∫ η, Z η ∂μ)`.
  - For `variance_identity_independent_copy_realN`, no separate source proof text for part (b) is available beyond the item 0.1 Euclidean expansion hint. As a derived proof strategy, expand `‖Z - Zcopy‖²`, integrate, use `h_ident` to identify the means and second moments of `Z` and `Zcopy`, and use `integral_inner_eq_inner_integral_of_indepFun hZ hZcopy h_indep` for the cross term. The right side reduces to twice the variance expression from part (a), then multiply by `(1/2 : ℝ)`.
