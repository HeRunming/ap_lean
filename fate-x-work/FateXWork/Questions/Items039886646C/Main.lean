import FateXWork.Questions.Shared.Probability

namespace FateXWork.Questions.Items039886646C

/-- The Bochner expectation of the random vector is the zero vector. -/
def HasMeanZero {Ω : Type*} [MeasurableSpace Ω] {μ : MeasureTheory.Measure Ω}
    {n : ℕ} (X : Ω → FateXWork.Questions.Shared.RealN n) : Prop :=
  (∫ ω, X ω ∂μ) = 0

private lemma norm_sq_sum_eq_sum_inner
    {k n : ℕ} (Z : Fin k → FateXWork.Questions.Shared.RealN n) :
    ‖∑ j, Z j‖ ^ 2 = ∑ i, ∑ j, inner ℝ (Z i) (Z j) := by
  rw [← real_inner_self_eq_norm_sq]
  rw [sum_inner]
  simp_rw [inner_sum]

/--
Source proof: expand the squared norm of the finite sum as a double sum of inner
products.  Independence and the zero-mean hypotheses make every off-diagonal
expectation vanish; diagonal terms are the right-hand summands.
-/
theorem integral_norm_sq_sum_eq_sum_integral_norm_sq
    {Ω : Type*} [MeasurableSpace Ω] {μ : MeasureTheory.Measure Ω}
    [MeasureTheory.IsProbabilityMeasure μ]
    {k n : ℕ} (Z : Fin k → Ω → FateXWork.Questions.Shared.RealN n)
    (h_integrable : ∀ j, MeasureTheory.Integrable (Z j) μ)
    (h_sq_integrable : ∀ j, MeasureTheory.Integrable (fun ω => ‖Z j ω‖ ^ 2) μ)
    (h_indep : Pairwise (fun i j => ProbabilityTheory.IndepFun (Z i) (Z j) μ))
    (h_mean_zero : ∀ j, HasMeanZero (μ := μ) (Z j)) :
    (∫ ω, ‖∑ j, Z j ω‖ ^ 2 ∂μ) = ∑ j, ∫ ω, ‖Z j ω‖ ^ 2 ∂μ := by
  have h_inner_integrable (i j : Fin k) :
      MeasureTheory.Integrable (fun ω => inner ℝ (Z i ω) (Z j ω)) μ := by
    by_cases hij : i = j
    · subst j
      simpa [real_inner_self_eq_norm_sq] using h_sq_integrable i
    · exact FateXWork.Questions.Shared.integrable_inner_of_indepFun
        (h_integrable i) (h_integrable j) (h_indep hij)
  have h_inner_sum_integrable (i : Fin k) :
      MeasureTheory.Integrable (fun ω => ∑ j, inner ℝ (Z i ω) (Z j ω)) μ := by
    induction (Finset.univ : Finset (Fin k)) using Finset.induction_on with
    | empty => simp
    | insert j s hjs ih =>
        simpa [Finset.sum_insert hjs] using (h_inner_integrable i j).add ih
  have h_inner_integral (i j : Fin k) :
      (∫ ω, inner ℝ (Z i ω) (Z j ω) ∂μ) =
        if i = j then ∫ ω, ‖Z i ω‖ ^ 2 ∂μ else 0 := by
    by_cases hij : i = j
    · subst j
      simp
    · have h_indep_ij : ProbabilityTheory.IndepFun (Z i) (Z j) μ := h_indep hij
      have hi0 : (∫ ω, Z i ω ∂μ) = 0 := h_mean_zero i
      have hj0 : (∫ ω, Z j ω ∂μ) = 0 := h_mean_zero j
      unfold HasMeanZero at hi0 hj0
      rw [if_neg hij]
      rw [FateXWork.Questions.Shared.integral_inner_eq_inner_integral_of_indepFun
        (h_integrable i) (h_integrable j) h_indep_ij]
      rw [hi0, hj0]
      simp
  calc
    (∫ ω, ‖∑ j, Z j ω‖ ^ 2 ∂μ)
        = ∫ ω, ∑ i, ∑ j, inner ℝ (Z i ω) (Z j ω) ∂μ := by
          apply MeasureTheory.integral_congr_ae
          filter_upwards [] with ω
          simpa using norm_sq_sum_eq_sum_inner (fun j : Fin k => Z j ω)
    _ = ∑ i, ∑ j, ∫ ω, inner ℝ (Z i ω) (Z j ω) ∂μ := by
          rw [MeasureTheory.integral_finset_sum]
          · apply Finset.sum_congr rfl
            intro i _
            rw [MeasureTheory.integral_finset_sum]
            intro j _
            exact h_inner_integrable i j
          · intro i _
            exact h_inner_sum_integrable i
    _ = ∑ i, ∑ j, (if i = j then ∫ ω, ‖Z i ω‖ ^ 2 ∂μ else 0) := by
          apply Finset.sum_congr rfl
          intro i _
          apply Finset.sum_congr rfl
          intro j _
          exact h_inner_integral i j
    _ = ∑ i, ∫ ω, ‖Z i ω‖ ^ 2 ∂μ := by
          apply Finset.sum_congr rfl
          intro i _
          simp

end FateXWork.Questions.Items039886646C
