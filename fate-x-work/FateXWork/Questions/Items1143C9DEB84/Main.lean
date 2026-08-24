import Mathlib.Probability.Integration
import Mathlib.Analysis.SpecialFunctions.Pow.Real

open scoped BigOperators ENNReal
open MeasureTheory

theorem expectation_finset_rpow_bounds
    {Ω : Type*} [MeasurableSpace Ω]
    (μ : Measure Ω) [IsProbabilityMeasure μ]
    (n : ℕ) (hn : 0 < n)
    (X : Fin n → Ω → ENNReal) (p : ℝ)
    (hX_measurable : ∀ i, Measurable (X i))
    (hp : 1 ≤ p) :
    ENNReal.rpow (∑ i, ENNReal.rpow (∫⁻ x, X i x ∂μ) p) (1 / p)
        ≤ ∫⁻ x, ENNReal.rpow (∑ i, ENNReal.rpow (X i x) p) (1 / p) ∂μ
      ∧
    (∫⁻ x, ENNReal.rpow (∑ i, ENNReal.rpow (X i x) p) (1 / p) ∂μ)
        ≤ ENNReal.rpow (∑ i, ∫⁻ x, ENNReal.rpow (X i x) p ∂μ) (1 / p) := by
  sorry
