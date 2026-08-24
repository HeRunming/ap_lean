import Mathlib

/-- A convex function has the same supremum on the convex hull of a set as on the set itself. -/
theorem maximum_principle
    {n : ℕ} (f : (Fin n → ℝ) → ℝ) (T : Set (Fin n → ℝ))
    (hf : ConvexOn ℝ Set.univ f) :
    sSup (f '' convexHull ℝ T) = sSup (f '' T) := by
  sorry
