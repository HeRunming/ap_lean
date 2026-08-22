import FateXWork.Questions.Shared.Analysis

namespace FateXWork.Questions.Items0438222EE4

open FateXWork.Questions.Shared
open scoped BigOperators

/--
Source proof: choose independent random signs and compute the expected squared norm;
mixed inner-product terms cancel and the remaining sum is at most `n`.
Prover notes: obtain an outcome of squared norm at most that expectation, then use
square-root order facts to conclude the displayed norm bound.
-/
theorem balancing_vectors_exists (n : ℕ) (x : Fin n → RealN n)
    (hx : ∀ i, ‖x i‖ ≤ 1) :
    ∃ ε : Fin n → ℝ,
      (∀ i, ε i = 1 ∨ ε i = -1) ∧
        ‖∑ i, ε i • x i‖ ≤ Real.sqrt (n : ℝ) := by
  sorry

/--
Source proof: take the coordinate unit vectors in `ℝⁿ`; every signed sum has all
coordinates equal to `±1`, hence its norm is exactly `√n`.
Prover notes: use the standard Euclidean coordinate basis and
`EuclideanSpace.norm_sq_eq`.
-/
theorem balancing_vectors_sharp (n : ℕ) :
    ∃ x : Fin n → RealN n,
      (∀ i, ‖x i‖ ≤ 1) ∧
        ∀ ε : Fin n → ℝ,
          (∀ i, ε i = 1 ∨ ε i = -1) →
            ‖∑ i, ε i • x i‖ = Real.sqrt (n : ℝ) := by
  sorry

end FateXWork.Questions.Items0438222EE4
