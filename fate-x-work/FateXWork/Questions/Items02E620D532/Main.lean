import FateXWork.Questions.Shared.Analysis

noncomputable section

open MeasureTheory
open FateXWork.Questions.Shared

namespace FateXWork.Questions.Items02E620D532

private lemma integral_norm_sub_sq_realN
    {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω} [IsProbabilityMeasure μ]
    {n : ℕ} (Z : Ω → RealN n)
    (hZ : Integrable Z μ)
    (hZ_sq : Integrable (fun ω => ‖Z ω‖ ^ 2) μ)
    (c : RealN n) :
    (∫ ω, ‖Z ω - c‖ ^ 2 ∂μ)
      = (∫ ω, ‖Z ω‖ ^ 2 ∂μ)
          - 2 * inner ℝ (∫ ω, Z ω ∂μ) c + ‖c‖ ^ 2 := by
  have hinner : Integrable (fun ω => inner ℝ (Z ω) c) μ := hZ.inner_const c
  have htwo : Integrable (fun ω => 2 * inner ℝ (Z ω) c) μ := hinner.const_mul 2
  have hint :
      (∫ ω, inner ℝ (Z ω) c ∂μ) = inner ℝ (∫ ω, Z ω ∂μ) c := by
    simpa [real_inner_comm] using ((innerSL ℝ c).integral_comp_comm hZ)
  have hadd :
      (∫ ω, (‖Z ω‖ ^ 2 - 2 * inner ℝ (Z ω) c) + ‖c‖ ^ 2 ∂μ) =
        (∫ ω, ‖Z ω‖ ^ 2 - 2 * inner ℝ (Z ω) c ∂μ) +
          (∫ _ : Ω, ‖c‖ ^ 2 ∂μ) := by
    simpa only [Pi.add_apply, Pi.sub_apply] using
      (integral_add (hZ_sq.sub htwo) (integrable_const (c := ‖c‖ ^ 2)))
  have hsub :
      (∫ ω, ‖Z ω‖ ^ 2 - 2 * inner ℝ (Z ω) c ∂μ) =
        (∫ ω, ‖Z ω‖ ^ 2 ∂μ) - (∫ ω, 2 * inner ℝ (Z ω) c ∂μ) := by
    simpa only [Pi.sub_apply] using (integral_sub hZ_sq htwo)
  have hmul :
      (∫ ω, 2 * inner ℝ (Z ω) c ∂μ) =
        2 * inner ℝ (∫ ω, Z ω ∂μ) c := by
    rw [integral_const_mul, hint]
  calc
    (∫ ω, ‖Z ω - c‖ ^ 2 ∂μ) =
        ∫ ω, (‖Z ω‖ ^ 2 - 2 * inner ℝ (Z ω) c) + ‖c‖ ^ 2 ∂μ := by
      congr 1
      funext ω
      exact norm_sub_pow_two_real (Z ω) c
    _ = (∫ ω, ‖Z ω‖ ^ 2 - 2 * inner ℝ (Z ω) c ∂μ) +
          (∫ _ : Ω, ‖c‖ ^ 2 ∂μ) := hadd
    _ = (∫ ω, ‖Z ω‖ ^ 2 ∂μ) - 2 * inner ℝ (∫ ω, Z ω ∂μ) c + ‖c‖ ^ 2 := by
      rw [hsub, hmul]
      simp

private lemma integral_inner_const_realN
    {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω}
    {n : ℕ} (Z : Ω → RealN n) (hZ : Integrable Z μ)
    (c : RealN n) :
    (∫ ω, inner ℝ (Z ω) c ∂μ) = inner ℝ (∫ ω, Z ω ∂μ) c := by
  simpa [real_inner_comm] using
    ((innerSL ℝ c).integral_comp_comm hZ)

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
  let m : RealN n := ∫ η, Z η ∂μ
  change
    (∫ ω, ‖Z ω - a‖ ^ 2 ∂μ) - (∫ ω, ‖Z ω - m‖ ^ 2 ∂μ) =
      ‖a - m‖ ^ 2
  rw [integral_norm_sub_sq_realN Z hZ hZ_sq a,
    integral_norm_sub_sq_realN Z hZ hZ_sq m,
    norm_sub_pow_two_real, real_inner_self_eq_norm_sq, real_inner_comm a m]
  ring

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
  constructor
  · exact ⟨∫ η, Z η ∂μ, rfl⟩
  · rintro b ⟨a, rfl⟩
    apply sub_nonneg.mp
    rw [mean_squared_error_decomposition_realN Z hZ hZ_sq a]
    positivity

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
  let m : ℝ := ∫ ω, X ω ∂μ
  have hcost (a : ℝ) :
      (∫ ω, (X ω - a) ^ 2 ∂μ) =
        (∫ ω, (X ω) ^ 2 ∂μ) - 2 * a * m + a ^ 2 := by
    have hlin : Integrable (fun ω => 2 * a * X ω) μ :=
      hX.const_mul (2 * a)
    have hconst : Integrable (fun _ : Ω => a ^ 2) μ := integrable_const _
    calc
      (∫ ω, (X ω - a) ^ 2 ∂μ) =
          ∫ ω, ((X ω) ^ 2 - 2 * a * X ω) + a ^ 2 ∂μ := by
        congr 1
        funext ω
        ring
      _ = (∫ ω, (X ω) ^ 2 - 2 * a * X ω ∂μ) +
            (∫ _ : Ω, a ^ 2 ∂μ) := by
        simpa only [Pi.add_apply, Pi.sub_apply] using
          (integral_add (hX_sq.sub hlin) hconst)
      _ = (∫ ω, (X ω) ^ 2 ∂μ) -
            (∫ ω, 2 * a * X ω ∂μ) + (∫ _ : Ω, a ^ 2 ∂μ) := by
        rw [integral_sub hX_sq hlin]
      _ = (∫ ω, (X ω) ^ 2 ∂μ) - 2 * a * m + a ^ 2 := by
        rw [integral_const_mul]
        simp [m]
  have hvar :
      ProbabilityTheory.variance X μ = ∫ ω, (X ω - m) ^ 2 ∂μ := by
    simpa [m] using (ProbabilityTheory.variance_eq_integral hX.aemeasurable)
  constructor
  · refine ⟨m, ?_⟩
    exact hvar.symm
  · rintro b ⟨a, rfl⟩
    rw [hvar]
    change (∫ ω, (X ω - m) ^ 2 ∂μ) ≤ ∫ ω, (X ω - a) ^ 2 ∂μ
    rw [hcost a, hcost m]
    nlinarith [sq_nonneg (a - m)]

end FateXWork.Questions.Items02E620D532
