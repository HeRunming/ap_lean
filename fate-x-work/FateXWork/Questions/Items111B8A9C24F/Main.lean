import Mathlib.MeasureTheory.Function.LpSeminorm.CompareExp

open MeasureTheory
open scoped ENNReal

theorem kk_monotonicity_Lp_norm
    {Ω : Type*} [MeasurableSpace Ω] (μ : Measure Ω) [IsProbabilityMeasure μ]
    (X : Ω → ℝ) (hX : Measurable X)
    {p q : ℝ≥0∞} (hp : 0 ≤ p) (hpq : p ≤ q) (hq : q ≤ ∞) :
    eLpNorm X p μ ≤ eLpNorm X q μ := by exact eLpNorm_le_eLpNorm_of_exponent_le hpq hX.aestronglyMeasurable

theorem kk_Lp_norm_inequality_not_reversible
    (p q : ℝ≥0∞) (hp : 0 ≤ p) (hpq : p < q) (hq : q ≤ ∞) :
    ∃ (μ : Measure ℕ) (X : ℕ → ℝ),
      IsProbabilityMeasure μ ∧
      Measurable X ∧
      eLpNorm X p μ < ∞ ∧
      eLpNorm X q μ = ∞ := by sorry
