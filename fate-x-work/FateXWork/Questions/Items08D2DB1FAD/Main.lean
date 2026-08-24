import Mathlib

open MeasureTheory

/-- A random vector uniformly distributed on the Euclidean unit ball has expected norm
`n / (n + 1)`. -/
theorem expected_norm_uniform_unitBall
    (n : ℕ)
    {Ω : Type*} [MeasurableSpace Ω]
    (ℙ : MeasureTheory.Measure Ω)
    [MeasureTheory.IsProbabilityMeasure ℙ]
    (X : Ω → EuclideanSpace ℝ (Fin n))
    (hX_measurable : Measurable X)
    (hX_uniform :
      MeasureTheory.Measure.map X ℙ =
        (MeasureTheory.volume
            (Metric.closedBall (0 : EuclideanSpace ℝ (Fin n)) 1))⁻¹ •
          MeasureTheory.volume.restrict
            (Metric.closedBall (0 : EuclideanSpace ℝ (Fin n)) 1)) :
    ∫ ω, ‖X ω‖ ∂ℙ = (n : ℝ) / ((n : ℝ) + 1) := by
  sorry
