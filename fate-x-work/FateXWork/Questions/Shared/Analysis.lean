import Mathlib

/-! Verified Analysis definitions and lemmas for this corpus. -/

namespace FateXWork.Questions.Shared

/-- The book's `ℝⁿ`, represented as Mathlib's finite-dimensional Euclidean space. -/
abbrev RealN (n : ℕ) := EuclideanSpace ℝ (Fin n)

end FateXWork.Questions.Shared
