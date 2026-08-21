import FateXWork.Questions.Shared.Probability

noncomputable section

open scoped BigOperators
open MeasureTheory ProbabilityTheory
open FateXWork.Questions.Shared

namespace FateXWork.Questions.Items018353C612

/--
Source proof: the reference solution recalls the Euclidean expansion
`‖x - y‖₂² = ‖x‖₂² - 2⟪x, y⟫ + ‖y‖₂²`, obtained by expanding
`⟪x - y, x - y⟫`.
Prover notes: unfold the squared norm through the real inner product, use
bilinearity and symmetry of `inner ℝ`, and simplify the resulting polynomial
identity.
-/
lemma norm_sub_sq_realN {n : ℕ} (x y : RealN n) :
    ‖x - y‖ ^ 2 = ‖x‖ ^ 2 - 2 * inner ℝ x y + ‖y‖ ^ 2 := by
  sorry

/--
Source proof: apply the preceding expansion pointwise with
`y = 𝔼 Z = ∫ η, Z η ∂μ`, then integrate. The cross term integrates to the
inner product of the mean with itself, and the integral of the constant
`‖𝔼 Z‖²` is that constant because `μ` is a probability measure.
Prover notes: use `norm_sub_sq_realN`, Bochner integral linearity for real
integrands, and `integral_const`/`measure_univ` simplifications for probability
measures.
-/
theorem variance_identity_realN
    {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω} [IsProbabilityMeasure μ]
    {n : ℕ} (Z : Ω → RealN n)
    (hZ : Integrable Z μ)
    (hZ_sq : Integrable (fun ω => ‖Z ω‖ ^ 2) μ) :
    (∫ ω, ‖Z ω - (∫ η, Z η ∂μ)‖ ^ 2 ∂μ)
      = (∫ ω, ‖Z ω‖ ^ 2 ∂μ) - ‖(∫ η, Z η ∂μ)‖ ^ 2 := by
  sorry

/--
No separate source proof text for part (b) is available beyond the item 0.1
Euclidean expansion hint.
Derived strategy: expand `‖Z - Z'‖₂²`, integrate, use that `Z'` has the same
distribution as `Z` to identify the two second moments and the two means, and
use independence to factor the mixed inner-product term. This makes
`𝔼 ‖Z - Z'‖²` equal to twice the variance expression from part (a).
Prover notes: combine `variance_identity_realN`, `IdentDistrib.integral_eq`
for the equal-distribution transfers, and the shared lemma
`integral_inner_eq_inner_integral_of_indepFun hZ hZcopy h_indep` for the
cross term.
-/
theorem variance_identity_independent_copy_realN
    {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω} [IsProbabilityMeasure μ]
    {n : ℕ} (Z Zcopy : Ω → RealN n)
    (hZ : Integrable Z μ) (hZcopy : Integrable Zcopy μ)
    (hZ_sq : Integrable (fun ω => ‖Z ω‖ ^ 2) μ)
    (hZcopy_sq : Integrable (fun ω => ‖Zcopy ω‖ ^ 2) μ)
    (h_indep : IndepFun Z Zcopy μ)
    (h_ident : IdentDistrib Z Zcopy μ μ) :
    (∫ ω, ‖Z ω - (∫ η, Z η ∂μ)‖ ^ 2 ∂μ)
      = (1 / 2 : ℝ) * (∫ ω, ‖Z ω - Zcopy ω‖ ^ 2 ∂μ) := by
  sorry

end FateXWork.Questions.Items018353C612
