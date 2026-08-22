import FateXWork.Questions.Shared.Probability

namespace FateXWork.Questions.Items039886646C

/-- The Bochner expectation of the random vector is the zero vector. -/
def HasMeanZero {Ω : Type*} [MeasurableSpace Ω] {μ : MeasureTheory.Measure Ω}
    {n : ℕ} (X : Ω → FateXWork.Questions.Shared.RealN n) : Prop :=
  (∫ ω, X ω ∂μ) = 0

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
  sorry

end FateXWork.Questions.Items039886646C
