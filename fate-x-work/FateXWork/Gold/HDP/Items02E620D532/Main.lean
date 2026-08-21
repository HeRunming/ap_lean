import FateXWork.Gold.HDP.Items018353C612.Main

open scoped ENNReal
open MeasureTheory ProbabilityTheory
open FateXWork.Questions.Shared

namespace FateXWork.Gold.HDP.Items02E620D532

/-- HDP, Question 0.2: the excess mean squared error of a fixed center `a` is exactly
the squared distance from `a` to the mean. -/
theorem meanSquaredError_sub_variance
    {Ω : Type*} [MeasurableSpace Ω]
    (μ : Measure Ω) [IsProbabilityMeasure μ]
    {n : ℕ} (Z : Ω → RealN n) (hZ : MemLp Z 2 μ) (a : RealN n) :
    (∫ ω, ‖Z ω - a‖ ^ 2 ∂μ) -
        (∫ ω, ‖Z ω - ∫ x, Z x ∂μ‖ ^ 2 ∂μ) =
      ‖a - ∫ x, Z x ∂μ‖ ^ 2 := by
  let m : RealN n := ∫ x, Z x ∂μ
  have hZ_int : Integrable Z μ := hZ.integrable (by norm_num)
  have hnorm : Integrable (fun x => ‖Z x‖ ^ 2) μ :=
    hZ.integrable_norm_pow (by norm_num)
  have hinner : Integrable (fun x => inner ℝ (Z x) a) μ := hZ_int.inner_const a
  have hconst : Integrable (fun _ : Ω => ‖a‖ ^ 2) μ := integrable_const _
  have hdiff_integral :
      (∫ ω, ‖Z ω‖ ^ 2 - 2 * inner ℝ (Z ω) a ∂μ) =
        (∫ ω, ‖Z ω‖ ^ 2 ∂μ) - 2 * (∫ ω, inner ℝ (Z ω) a ∂μ) := by
    calc
      _ = (∫ ω, ‖Z ω‖ ^ 2 ∂μ) - ∫ ω, 2 * inner ℝ (Z ω) a ∂μ :=
        integral_sub hnorm (hinner.const_mul 2)
      _ = _ := by rw [integral_const_mul]
  have hinner_integral : (∫ ω, inner ℝ (Z ω) a ∂μ) = inner ℝ m a := by
    calc
      _ = ∫ ω, inner ℝ a (Z ω) ∂μ := by
        apply integral_congr_ae
        filter_upwards [] with ω
        exact real_inner_comm a (Z ω)
      _ = inner ℝ a (∫ ω, Z ω ∂μ) := integral_inner hZ_int a
      _ = inner ℝ m a := by rw [real_inner_comm]
  have hshift :
      (∫ ω, ‖Z ω - a‖ ^ 2 ∂μ) =
        (∫ ω, ‖Z ω‖ ^ 2 ∂μ) - 2 * inner ℝ m a + ‖a‖ ^ 2 := by
    calc
      _ = ∫ ω, (‖Z ω‖ ^ 2 - 2 * inner ℝ (Z ω) a) + ‖a‖ ^ 2 ∂μ := by
        apply integral_congr_ae
        filter_upwards [] with ω
        exact norm_sub_pow_two_real (Z ω) a
      _ = (∫ ω, ‖Z ω‖ ^ 2 - 2 * inner ℝ (Z ω) a ∂μ) +
            (∫ _ : Ω, ‖a‖ ^ 2 ∂μ) := by
        change
          (∫ ω,
              ((fun x => ‖Z x‖ ^ 2) - (fun x => 2 * inner ℝ (Z x) a)) ω +
                (fun _ : Ω => ‖a‖ ^ 2) ω ∂μ) = _
        rw [integral_add (hnorm.sub (hinner.const_mul 2)) hconst]
        rfl
      _ = _ := by
        rw [hdiff_integral, hinner_integral, integral_const]
        simp
  rw [hshift]
  rw [FateXWork.Gold.HDP.Items018353C612.vectorVariance_eq_integral_norm_sq_sub_norm_mean_sq μ Z hZ]
  rw [norm_sub_pow_two_real]
  rw [real_inner_comm a m]
  ring

/-- HDP, Question 0.2: expectation minimizes mean squared Euclidean error, expressed
as the least element of the range of all constant-center risks. -/
theorem expectation_minimizes_meanSquaredError
    {Ω : Type*} [MeasurableSpace Ω]
    (μ : Measure Ω) [IsProbabilityMeasure μ]
    {n : ℕ} (Z : Ω → RealN n) (hZ : MemLp Z 2 μ) :
    IsLeast
      (Set.range fun a : RealN n => ∫ ω, ‖Z ω - a‖ ^ 2 ∂μ)
      (∫ ω, ‖Z ω - ∫ x, Z x ∂μ‖ ^ 2 ∂μ) := by
  constructor
  · exact ⟨∫ x, Z x ∂μ, rfl⟩
  · intro risk hrisk
    rcases hrisk with ⟨a, rfl⟩
    have h := meanSquaredError_sub_variance μ Z hZ a
    nlinarith [sq_nonneg ‖a - ∫ x, Z x ∂μ‖]

end FateXWork.Gold.HDP.Items02E620D532
