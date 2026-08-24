import Mathlib.Probability.Moments.MGFAnalytic

open MeasureTheory

/-- A random variable with finite subgaussian moment generating function bounds
for every real coefficient has zero mean. -/
theorem subgaussian_mgf_requires_zero_mean
    {Ω : Type*} [MeasurableSpace Ω] (μ : Measure Ω)
    [IsProbabilityMeasure μ] (X : Ω → ℝ) (K : ℝ)
    (hX_measurable : Measurable X) (hX_integrable : Integrable X μ)
    (hmgf_integrable : ∀ coeff : ℝ,
      Integrable (fun ω => Real.exp (coeff * X ω)) μ)
    (hmgf : ∀ coeff : ℝ,
      (∫ ω, Real.exp (coeff * X ω) ∂μ) ≤
        Real.exp (K ^ 2 * coeff ^ 2)) :
    (∫ ω, X ω ∂μ) = 0 := by
  sorry
