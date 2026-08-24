import Mathlib.MeasureTheory.Function.LpSeminorm.CompareExp

universe u v

open MeasureTheory

theorem interpolation_L1_Linfty
    {Ω : Type u} {E : Type v} [MeasurableSpace Ω] [NormedAddCommGroup E]
    (μ : Measure Ω) (X : Ω → E) (p : ENNReal)
    (hX : AEStronglyMeasurable X μ) (hp_one : 1 < p) (hp_top : p < (⊤ : ENNReal)) :
    eLpNorm X p μ ≤
      (eLpNorm X 1 μ).rpow (p.toReal⁻¹) *
        (eLpNorm X ⊤ μ).rpow (1 - p.toReal⁻¹) := by
  sorry
