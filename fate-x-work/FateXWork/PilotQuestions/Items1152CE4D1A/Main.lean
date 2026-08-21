import Mathlib

namespace FateXWork
namespace PilotQuestions
namespace Items1152CE4D1A

/-- Mathlib model for the source phrase `ℝ^n`. -/
abbrev RealN (n : ℕ) : Type := EuclideanSpace ℝ (Fin n)

/-- Mathlib model for the source notation `conv(T)` in `ℝ^n`. -/
abbrev convHullInRealN {n : ℕ} (T : Set (RealN n)) : Set (RealN n) :=
  convexHull ℝ T

/--
Source `HDP/source/pilot_questions.json`, item 1.1 (page locator 28):
"Consider any subset `T` of `ℝ^n`. Check that `conv(T)` is a convex set."

Source proof: no proof text is supplied in the JSON; both `answer` and `solution` are empty.
Proof sketch: `conv(T)` is formalized as Mathlib's `convexHull ℝ T`, and Mathlib theorem
`convex_convexHull ℝ T` states that the convex hull is convex.
Prover notes: after statement review, unfold `convHullInRealN` and apply `convex_convexHull`.
-/
theorem item_1_1_convex_convexHull {n : ℕ} (T : Set (RealN n)) :
    Convex ℝ (convHullInRealN T) := by
  exact convex_convexHull ℝ T

end Items1152CE4D1A
end PilotQuestions
end FateXWork
