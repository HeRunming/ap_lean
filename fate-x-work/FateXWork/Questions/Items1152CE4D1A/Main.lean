import Mathlib

/-- The convex hull of any subset of `ℝ^n` is convex. -/
theorem convex_convexHull_Rn (n : ℕ) (T : Set (Fin n → ℝ)) :
    Convex ℝ (convexHull ℝ T) := by exact convex_convexHull ℝ T
