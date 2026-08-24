import Mathlib.Probability.Moments.SubGaussian

open scoped BigOperators ENNReal NNReal
open MeasureTheory ProbabilityTheory

theorem hoeffding_inequality_for_bounded_random_variables
    {Ω : Type*} [MeasurableSpace Ω] (μ : Measure Ω) [IsProbabilityMeasure μ]
    {n : ℕ} (X : Fin n → Ω → ℝ) (a b : Fin n → ℝ)
    (h_indep : iIndepFun X μ)
    (h_meas : ∀ i, Measurable (X i))
    (h_bounded : ∀ i, ∀ᵐ ω ∂μ, X i ω ∈ Set.Icc (a i) (b i))
    {t : ℝ} (ht : 0 ≤ t) :
    μ.real {ω | t ≤ ∑ i : Fin n, (X i ω - ∫ x, X i x ∂μ)} ≤
      Real.exp (-2 * t ^ 2 / (∑ i : Fin n, (b i - a i) ^ 2)) := by
  sorry
