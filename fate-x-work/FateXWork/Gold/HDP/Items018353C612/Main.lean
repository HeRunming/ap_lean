import FateXWork.Questions.Shared.Probability

open scoped ENNReal
open MeasureTheory ProbabilityTheory
open FateXWork.Questions.Shared

namespace FateXWork.Gold.HDP.Items018353C612

/-- HDP, Question 0.1(a): the second central moment identity for a random vector. -/
theorem vectorVariance_eq_integral_norm_sq_sub_norm_mean_sq
    {Ω : Type*} [MeasurableSpace Ω]
    (μ : Measure Ω) [IsProbabilityMeasure μ]
    {n : ℕ} (Z : Ω → RealN n)
    (hZ : MemLp Z 2 μ) :
    (∫ ω, ‖Z ω - ∫ x, Z x ∂μ‖ ^ 2 ∂μ) =
      (∫ ω, ‖Z ω‖ ^ 2 ∂μ) - ‖∫ x, Z x ∂μ‖ ^ 2 := by
  let m : RealN n := ∫ x, Z x ∂μ
  have hZ_int : Integrable Z μ := hZ.integrable (by norm_num)
  have hnorm : Integrable (fun x => ‖Z x‖ ^ 2) μ :=
    hZ.integrable_norm_pow (by norm_num)
  have hinner : Integrable (fun x => inner ℝ (Z x) m) μ := hZ_int.inner_const m
  have hconst : Integrable (fun _ : Ω => ‖m‖ ^ 2) μ := integrable_const _
  have hdiff_integral :
      (∫ ω, ‖Z ω‖ ^ 2 - 2 * inner ℝ (Z ω) m ∂μ) =
        (∫ ω, ‖Z ω‖ ^ 2 ∂μ) - 2 * (∫ ω, inner ℝ (Z ω) m ∂μ) := by
    calc
      _ = (∫ ω, ‖Z ω‖ ^ 2 ∂μ) -
            ∫ ω, 2 * inner ℝ (Z ω) m ∂μ :=
        integral_sub hnorm (hinner.const_mul 2)
      _ = _ := by rw [integral_const_mul]
  calc
    (∫ ω, ‖Z ω - ∫ x, Z x ∂μ‖ ^ 2 ∂μ) =
        ∫ ω, (‖Z ω‖ ^ 2 - 2 * inner ℝ (Z ω) m) + ‖m‖ ^ 2 ∂μ := by
          apply integral_congr_ae
          filter_upwards [] with ω
          exact norm_sub_pow_two_real (Z ω) m
    _ = (∫ ω, ‖Z ω‖ ^ 2 ∂μ) -
          2 * (∫ ω, inner ℝ (Z ω) m ∂μ) +
          (∫ _ : Ω, ‖m‖ ^ 2 ∂μ) := by
          change
            (∫ ω,
                ((fun x => ‖Z x‖ ^ 2) - (fun x => 2 * inner ℝ (Z x) m)) ω +
                  (fun _ : Ω => ‖m‖ ^ 2) ω ∂μ) = _
          rw [integral_add (hnorm.sub (hinner.const_mul 2)) hconst]
          rw [show
            (∫ ω,
                ((fun x => ‖Z x‖ ^ 2) - (fun x => 2 * inner ℝ (Z x) m)) ω ∂μ) =
              (∫ ω, ‖Z ω‖ ^ 2 - 2 * inner ℝ (Z ω) m ∂μ) by rfl]
          rw [hdiff_integral]
    _ = (∫ ω, ‖Z ω‖ ^ 2 ∂μ) - ‖m‖ ^ 2 := by
          have hinner_integral :
              (∫ ω, inner ℝ (Z ω) m ∂μ) = inner ℝ m m := by
            calc
              (∫ ω, inner ℝ (Z ω) m ∂μ) =
                  ∫ ω, inner ℝ m (Z ω) ∂μ := by
                    apply integral_congr_ae
                    filter_upwards [] with ω
                    exact real_inner_comm m (Z ω)
              _ = inner ℝ m (∫ ω, Z ω ∂μ) := integral_inner hZ_int m
              _ = inner ℝ m m := by rfl
          rw [hinner_integral, integral_const]
          simp
          ring
    _ = (∫ ω, ‖Z ω‖ ^ 2 ∂μ) - ‖∫ x, Z x ∂μ‖ ^ 2 := by rfl

/-- HDP, Question 0.1(b): an independent identically distributed copy expresses
the same variance as half the expected squared pairwise distance. -/
theorem vectorVariance_eq_half_integral_norm_sub_independent_copy_sq
    {Ω : Type*} [MeasurableSpace Ω]
    (μ : Measure Ω) [IsProbabilityMeasure μ]
    {n : ℕ} (Z Z' : Ω → RealN n)
    (hZ : MemLp Z 2 μ)
    (h_indep : IndepFun Z Z' μ)
    (h_ident : IdentDistrib Z Z' μ μ) :
    (∫ ω, ‖Z ω - ∫ x, Z x ∂μ‖ ^ 2 ∂μ) =
      (1 / 2 : ℝ) * ∫ ω, ‖Z ω - Z' ω‖ ^ 2 ∂μ := by
  have hZ_int : Integrable Z μ := hZ.integrable (by norm_num)
  have hZ' : MemLp Z' 2 μ := h_ident.memLp_snd hZ
  have hZ'_int : Integrable Z' μ := hZ'.integrable (by norm_num)
  have hnormZ : Integrable (fun ω => ‖Z ω‖ ^ 2) μ :=
    hZ.integrable_norm_pow (by norm_num)
  have hnormZ' : Integrable (fun ω => ‖Z' ω‖ ^ 2) μ :=
    hZ'.integrable_norm_pow (by norm_num)
  have hinner : Integrable (fun ω => inner ℝ (Z ω) (Z' ω)) μ :=
    integrable_inner_of_indepFun hZ_int hZ'_int h_indep
  have hinner_integral :
      (∫ ω, inner ℝ (Z ω) (Z' ω) ∂μ) =
        inner ℝ (∫ ω, Z ω ∂μ) (∫ ω, Z' ω ∂μ) :=
    integral_inner_eq_inner_integral_of_indepFun hZ_int hZ'_int h_indep
  have hdiff_integral :
      (∫ ω, ‖Z ω‖ ^ 2 - 2 * inner ℝ (Z ω) (Z' ω) ∂μ) =
        (∫ ω, ‖Z ω‖ ^ 2 ∂μ) -
          2 * (∫ ω, inner ℝ (Z ω) (Z' ω) ∂μ) := by
    calc
      _ = (∫ ω, ‖Z ω‖ ^ 2 ∂μ) -
            ∫ ω, 2 * inner ℝ (Z ω) (Z' ω) ∂μ :=
        integral_sub hnormZ (hinner.const_mul 2)
      _ = _ := by rw [integral_const_mul]
  have hpair :
      (∫ ω, ‖Z ω - Z' ω‖ ^ 2 ∂μ) =
        (∫ ω, ‖Z ω‖ ^ 2 ∂μ) -
          2 * inner ℝ (∫ ω, Z ω ∂μ) (∫ ω, Z' ω ∂μ) +
          (∫ ω, ‖Z' ω‖ ^ 2 ∂μ) := by
    calc
      (∫ ω, ‖Z ω - Z' ω‖ ^ 2 ∂μ) =
          ∫ ω, (‖Z ω‖ ^ 2 - 2 * inner ℝ (Z ω) (Z' ω)) + ‖Z' ω‖ ^ 2 ∂μ := by
            apply integral_congr_ae
            filter_upwards [] with ω
            exact norm_sub_pow_two_real (Z ω) (Z' ω)
      _ = (∫ ω, ‖Z ω‖ ^ 2 - 2 * inner ℝ (Z ω) (Z' ω) ∂μ) +
            (∫ ω, ‖Z' ω‖ ^ 2 ∂μ) := by
              change
                (∫ ω,
                    ((fun x => ‖Z x‖ ^ 2) -
                      (fun x => 2 * inner ℝ (Z x) (Z' x))) ω +
                      (fun x => ‖Z' x‖ ^ 2) ω ∂μ) = _
              rw [integral_add (hnormZ.sub (hinner.const_mul 2)) hnormZ']
              rfl
      _ = _ := by rw [hdiff_integral, hinner_integral]
  have hnorm_eq :
      (∫ ω, ‖Z ω‖ ^ 2 ∂μ) = ∫ ω, ‖Z' ω‖ ^ 2 ∂μ := by
    have h := h_ident.comp (by fun_prop : Measurable fun x : RealN n => ‖x‖ ^ 2)
    simpa only [Function.comp_apply] using h.integral_eq
  have hmean_eq : (∫ ω, Z ω ∂μ) = ∫ ω, Z' ω ∂μ := h_ident.integral_eq
  rw [vectorVariance_eq_integral_norm_sq_sub_norm_mean_sq μ Z hZ, hpair]
  rw [← hnorm_eq, ← hmean_eq]
  simp only [real_inner_self_eq_norm_sq]
  ring

end FateXWork.Gold.HDP.Items018353C612
