# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Scoped item: `items-0.2`, source label `[0.2]`
- Target Lean entry file: `FateXWork/Questions/Items02E620D532/Main.lean`
- Planner status: source-backed declarations independently reviewed and ready for proof handoff.

## Planner Checklist

- [x] Identify definitions and notation that must exist before theorem statements.
- [x] Split the source result into Lean-sized declarations.
- [x] Record source labels and bounded-cache lines for every generated declaration.
- [x] Check local project and Mathlib names before introducing duplicates.
- [x] Compare the drafted Lean statements with the source at planner level.
- [x] Run independent statement/source verification review and apply corrections.
- [x] Attach all source proof text available in the bounded source slice.
- [x] Record a natural-language proof strategy for every theorem.
- [x] Resolve all construction stubs before proof handoff; no definition, structure, class, or instance stubs are planned.
- [x] Mark stable theorem/lemma/example `sorry` declarations ready for a user-started prove workflow. (Only check after independent review.)

## Import Plan

Direct Lean imports in generated Lean files:

- `FateXWork.Questions.Shared.Analysis`

## Root Project Coverage

- `FateXWork/Questions/Items02E620D532.lean` imports `FateXWork.Questions.Items02E620D532.Main`.
- `FateXWork.lean` imports `FateXWork.Questions.Items02E620D532`, so a plain project build covers the generated target.

## Suggested Search Modules

Non-gating modules or namespaces to search while proving; these are not direct imports unless a prover later demonstrates the need and updates the import plan.

- `Mathlib.Probability.Moments.Variance`
- `Mathlib.MeasureTheory.Integral.Bochner.Basic`
- `Mathlib.Analysis.InnerProductSpace.PiL2`
- `FateXWork.Questions.Items018353C612` for the already named vector variance identity interface, if direct reuse is preferable to a local expansion.

## Generated File Layout

- Aggregator and declaration file: `FateXWork/Questions/Items02E620D532/Main.lean`.
- No split files are planned. This scoped item has one auxiliary identity and two extremal-property theorems, so keeping the source context in one file is clearer.

## Draft Verification Notes

- `lean_inspect` reports no hard diagnostics in `Main.lean`; the only target warnings are the three intentional theorem `sorry` placeholders.
- `lean_verify(mode=project)` passed after the target and root imports were confirmed, building `FateXWork` successfully with only the intended target `sorry` warnings (plus pre-existing warnings in another generated item).
- The remaining handoff blocker is the required independent statement/source review; this drafting pass does not stamp that review.

## Local/Mathlib Search Notes

- `FateXWork.Questions.Shared.RealN n` is the project bridge for the source's `\mathbb{R}^n`; its verified interface is `EuclideanSpace ℝ (Fin n)` and is available from `FateXWork.Questions.Shared.Analysis`.
- Local outline inspection found `variance_identity_realN` in the previously generated item `FateXWork.Questions.Items018353C612.Main`. It may be useful during proving, but it is not a statement dependency and is therefore only a suggested search module here.
- Mathlib search found `ProbabilityTheory.variance_eq_integral`, which identifies scalar variance with the integral of squared deviation from the mean under an a.e.-measurability hypothesis.
- Mathlib search also found `ProbabilityTheory.variance_def'` and `ProbabilityTheory.variance_eq_sub` under a `MemLp X 2 μ` assumption. These are proof-search hints, not required direct imports in the draft.
- No existing local declaration for the complete mean-squared-error minimization statement was found under the available degraded project search surface.

## Source Statement Inventory

### 0.2

- Source label: `0.2`.
- Title: KKK (Expectation minimizes the mean squared error).
- Kind: question stating a scalar extremal property and asking for its high-dimensional version.
- Source locator: `HDP/source/full/qa/questions.json`; authoritative scoped cache `.leanflow/workflow-state/formalization/HDP-source-full-qa-questions/batches/items-0.2/extracted.txt`, lines 1-10; preflight source label `[0.2]`.
- Source statement:
  - The introductory scalar claim says that the variance of a real random variable is its minimum mean squared error over constant predictors.
  - The requested high-dimensional claim says that a random vector `Z` in `\mathbb{R}^n` with finite `𝔼 ‖Z‖₂²` satisfies
    `𝔼 ‖Z - 𝔼 Z‖₂² = min_{a ∈ ℝⁿ} 𝔼 ‖Z - a‖₂²`.
- Planned Lean declarations:

  ```lean
  theorem mean_squared_error_decomposition_realN
      {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω} [IsProbabilityMeasure μ]
      {n : ℕ} (Z : Ω → RealN n)
      (hZ : Integrable Z μ)
      (hZ_sq : Integrable (fun ω => ‖Z ω‖ ^ 2) μ)
      (a : RealN n) :
      (∫ ω, ‖Z ω - a‖ ^ 2 ∂μ)
          - (∫ ω, ‖Z ω - ∫ η, Z η ∂μ‖ ^ 2 ∂μ)
        = ‖a - ∫ η, Z η ∂μ‖ ^ 2

  theorem expectation_minimizes_mean_squared_error_realN
      {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω} [IsProbabilityMeasure μ]
      {n : ℕ} (Z : Ω → RealN n)
      (hZ : Integrable Z μ)
      (hZ_sq : Integrable (fun ω => ‖Z ω‖ ^ 2) μ) :
      IsLeast
        (Set.range (fun a : RealN n => ∫ ω, ‖Z ω - a‖ ^ 2 ∂μ))
        (∫ ω, ‖Z ω - ∫ η, Z η ∂μ‖ ^ 2 ∂μ)

  theorem variance_is_minimum_mean_squared_error_real
      {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω} [IsProbabilityMeasure μ]
      (X : Ω → ℝ)
      (hX : Integrable X μ)
      (hX_sq : Integrable (fun ω => (X ω) ^ 2) μ) :
      IsLeast
        (Set.range (fun a : ℝ => ∫ ω, (X ω - a) ^ 2 ∂μ))
        (ProbabilityTheory.variance X μ)
  ```

- Dependencies:
  - `RealN` for the source representation `\mathbb{R}^n`.
  - `Measure`, `IsProbabilityMeasure`, Bochner/Lebesgue integral notation, and `Integrable` for expectations and finite moments.
  - `Set.range` and `IsLeast` to encode an attained minimum without choosing a separate `min` operator on functions.
  - `ProbabilityTheory.variance` for the introductory scalar extremal property.
  - The main extremal theorem depends mathematically on `mean_squared_error_decomposition_realN`; the scalar theorem uses the analogous one-dimensional decomposition and the variance-as-centered-integral identity.
- Formal statement review:
  - `mean_squared_error_decomposition_realN` formalizes exactly the identity supplied by the source proof excerpt, with `μ_Z = ∫ η, Z η ∂μ` substituted directly in the formula.
  - `expectation_minimizes_mean_squared_error_realN` encodes the displayed minimum as `IsLeast (Set.range cost) centeredCost`. Membership in the range records attainment at `a = 𝔼 Z`; the lower-bound component records that no constant `a` has lower mean squared error. Thus it expresses the equality with an attained minimum rather than only an inequality.
  - `variance_is_minimum_mean_squared_error_real` directly records the introductory scalar variance property, avoiding reliance on an unstated equivalence between `ℝ` and the one-dimensional `RealN 1` representation.
  - The scalar display in the source typesets `𝔼 min_a (X-a)^2`, while the title, the phrase “expectation minimizes the mean squared error,” and the following high-dimensional display make the mathematically coherent intended quantifier order `min_a 𝔼 (X-a)^2`. The Lean scalar theorem follows that clarified reading; a literal pointwise minimum would be zero and would not equal variance in general.
- Source qualifiers:
  - Mathematical object classes: a real random variable for the introductory claim and a random vector in `\mathbb{R}^n` for the requested generalization.
  - Quantifier order: arbitrary probability space, arbitrary dimension `n`, arbitrary square-integrable random vector `Z`, then minimization over deterministic constants `a`; analogously, arbitrary real random variable `X`, then real constants `a`.
  - Parameter domains and codomains: `Z : Ω → RealN n`, `a : RealN n`; `X : Ω → ℝ`, scalar `a : ℝ`; every cost and minimum value lies in `ℝ`.
  - Equality/minimum condition: the centered mean squared error is an attained least element of the range of the constant-predictor cost function. The scalar least value is `ProbabilityTheory.variance X μ`.
  - Side conditions: the source explicitly requires finite `𝔼 ‖Z‖₂²` and implicitly treats `Z` as a measurable random vector on a probability space. Lean makes the probability measure, Bochner integrability, and integrability of the squared norm explicit. The scalar companion states corresponding finite first- and second-moment assumptions.
  - Follow-on claims: no uniqueness claim for the minimizing constant is stated in the source, so none is added. The source proof identity is retained as a separate auxiliary theorem.
- Lean coverage:
  - `mean_squared_error_decomposition_realN` covers the complete displayed identity in the reference solution.
  - `expectation_minimizes_mean_squared_error_realN` covers the requested high-dimensional minimum statement, including attainment at the expectation through `IsLeast` membership.
  - `variance_is_minimum_mean_squared_error_real` covers the introductory scalar variance property directly in the source's scalar representation.
  - `RealN n = EuclideanSpace ℝ (Fin n)` is the explicit shared representation bridge for `\mathbb{R}^n`.
- Scope changes:
  - An explicit measurable probability space `(Ω, μ)` and `[IsProbabilityMeasure μ]` are added because the source uses ambient expectation notation.
  - `Integrable Z μ` and integrability of `ω ↦ ‖Z ω‖²` make the random-vector measurability/finite-moment convention explicit. Over a probability space, finite second moment mathematically entails finite first moment; the separate first-moment hypothesis keeps the Bochner expectation interface explicit and matches nearby project conventions.
  - The scalar companion similarly makes finite first and second moments explicit; these are the finite-variance conditions implicit in the introductory formula.
  - The source's `\mathbb{R}^n` is represented by the shared abbreviation `RealN n = EuclideanSpace ℝ (Fin n)`; no coordinate-level weakening is made.
  - The ambiguous scalar typesetting is interpreted as minimization outside expectation, as forced by the title and the unambiguous high-dimensional formula. No uniqueness theorem is added.
- Statement verification status: approved by independent read-only Codex `gpt-5.6-terra` review; see `IndependentReview.md`.
- Complete source proof text available in the scoped source slice:

  > 0.2 Check the identity `𝔼 ‖Z - a‖₂² - 𝔼 ‖Z - μ‖₂² = ‖a - μ‖₂²` where `μ = 𝔼 Z`.

  This is all proof text available in the authoritative bounded source slice; no longer proof is present there.
- Source proof / prover notes:
  - For `mean_squared_error_decomposition_realN`, write `m = ∫ η, Z η ∂μ` and expand `‖Z ω - a‖²` as `‖(Z ω - m) + (m - a)‖²`. After integration, the cross term is zero because `∫ (Z - m) = 0` under a probability measure. The remaining constant term integrates to `‖m - a‖² = ‖a - m‖²`.
  - A second route is to expand both costs using the vector variance identity `∫ ‖Z-c‖² = ∫ ‖Z‖² - 2⟪∫ Z,c⟫ + ‖c‖²`, subtract, and simplify with `c = m`. The nearby `variance_identity_realN` interface may discharge the centered part after an import is justified.
  - For `expectation_minimizes_mean_squared_error_realN`, witness range membership with `a = ∫ η, Z η ∂μ`. For any `a`, rearrange `mean_squared_error_decomposition_realN`; nonnegativity of `‖a - 𝔼 Z‖²` gives the lower bound.
  - For `variance_is_minimum_mean_squared_error_real`, use `ProbabilityTheory.variance_eq_integral hX.aemeasurable` to identify variance with the centered scalar cost, then repeat the decomposition/nonnegativity argument in `ℝ` (or derive the scalar calculation by `ring` after integral linearity).
