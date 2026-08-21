import Mathlib

namespace HDP

/-!
# High-Dimensional Probability pilot formalization

Source: `HDP-2.pdf`, Exercise 1.1, physical PDF page 28, recovered from the
parser-side provenance manifest. The source solution is empty; the Lean proof
may use any verified Mathlib route.
-/

/-- Exercise 1.1: the convex hull of any subset of `ℝⁿ` is convex. -/
theorem exercise_1_1 {n : ℕ} (T : Set (EuclideanSpace ℝ (Fin n))) :
    Convex ℝ (convexHull ℝ T) := by
  exact convex_convexHull ℝ T

end HDP
