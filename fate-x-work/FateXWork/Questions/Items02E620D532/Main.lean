import FateXWork.Questions.Shared.Analysis

noncomputable section

open MeasureTheory
open FateXWork.Questions.Shared

namespace FateXWork.Questions.Items02E620D532

/--
Source proof: with `m = 𝔼 Z`, check
`𝔼 ‖Z - a‖₂² - 𝔼 ‖Z - m‖₂² = ‖a - m‖₂²`.
Prover notes: expand around `m`; the mixed term integrates to zero because
`∫ (Z - m) = 0`, and the remaining constant term is the squared distance
from `a` to `m`.
-/
theorem mean_squared_error_decomposition_realN
    {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω} [IsProbabilityMeasure μ]
    {n : ℕ} (Z : Ω → RealN n)
    (hZ : Integrable Z μ)
    (hZ_sq : Integrable (fun ω => ‖Z ω‖ ^ 2) μ)
    (a : RealN n) :
    (∫ ω, ‖Z ω - a‖ ^ 2 ∂μ)
        - (∫ ω, ‖Z ω - ∫ η, Z η ∂μ‖ ^ 2 ∂μ)
      = ‖a - ∫ η, Z η ∂μ‖ ^ 2 := by
  sorry

/--
Source proof: apply the preceding decomposition identity. Its right-hand
side is nonnegative, so the centered cost is no larger than the cost at any
constant `a`; choosing `a = 𝔼 Z` shows that this lower bound is attained.
Prover notes: construct `IsLeast` from the range witness `𝔼 Z` and the
pointwise lower bound obtained by rearranging the decomposition identity.
-/
theorem expectation_minimizes_mean_squared_error_realN
    {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω} [IsProbabilityMeasure μ]
    {n : ℕ} (Z : Ω → RealN n)
    (hZ : Integrable Z μ)
    (hZ_sq : Integrable (fun ω => ‖Z ω‖ ^ 2) μ) :
    IsLeast
      (Set.range (fun a : RealN n => ∫ ω, ‖Z ω - a‖ ^ 2 ∂μ))
      (∫ ω, ‖Z ω - ∫ η, Z η ∂μ‖ ^ 2 ∂μ) := by
  sorry

/--
Source proof: the scalar variance is the centered squared-error integral, and
the same decomposition gives `𝔼 (X-a)² = Var(X) + (a-𝔼 X)²`.
Prover notes: rewrite variance with `ProbabilityTheory.variance_eq_integral`,
witness attainment at `a = 𝔼 X`, and use nonnegativity of the final square
for the lower-bound component of `IsLeast`.
-/
theorem variance_is_minimum_mean_squared_error_real
    {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω} [IsProbabilityMeasure μ]
    (X : Ω → ℝ)
    (hX : Integrable X μ)
    (hX_sq : Integrable (fun ω => (X ω) ^ 2) μ) :
    IsLeast
      (Set.range (fun a : ℝ => ∫ ω, (X ω - a) ^ 2 ∂μ))
      (ProbabilityTheory.variance X μ) := by
  sorry

end FateXWork.Questions.Items02E620D532
