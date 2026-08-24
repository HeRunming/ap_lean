import Mathlib

open scoped BigOperators
open MeasureTheory

/-- A function on a convex subset is convex exactly when it satisfies all finite
weighted Jensen inequalities. -/
theorem convex_subtype_iff_finite_jensen
    {n : ℕ} {K : Set (Fin n → ℝ)} (hK : Convex ℝ K)
    (f : K → ℝ) :
    (∀ (x y : K) (a b : ℝ) (z : K),
        0 ≤ a →
        0 ≤ b →
        a + b = 1 →
        (z : Fin n → ℝ) = a • (x : Fin n → ℝ) + b • (y : Fin n → ℝ) →
        f z ≤ a * f x + b * f y) ↔
      ∀ (m : ℕ) (x : Fin m → K) (coeff : Fin m → ℝ) (z : K),
        (∀ i, 0 ≤ coeff i) →
        (∑ i : Fin m, coeff i) = 1 →
        (z : Fin n → ℝ) = ∑ i : Fin m, coeff i • (x i : Fin n → ℝ) →
        f z ≤ ∑ i : Fin m, coeff i * f (x i) := by
  sorry

/-- Jensen's inequality for a finitely valued random vector. -/
theorem jensen_finite_random_vector
    {Ω : Type*} [MeasurableSpace Ω]
    (μ : Measure Ω) [IsProbabilityMeasure μ]
    {n : ℕ} (X : Ω → Fin n → ℝ)
    (f : (Fin n → ℝ) → ℝ)
    (hf : ConvexOn ℝ Set.univ f)
    (hX_measurable : Measurable X)
    (hX_finite_range : (Set.range X).Finite) :
    f (∫ ω, X ω ∂μ) ≤ ∫ ω, f (X ω) ∂μ := by
  sorry
