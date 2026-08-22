import FateXWork.Questions.Shared.Analysis

noncomputable section

open scoped BigOperators
open FateXWork.Questions.Shared

namespace FateXWork.Questions.Items05784E1F74

/-- The standard basis vertex `e_i` of the source simplex in `ℝⁿ`. -/
def standardSimplexVertex {n : ℕ} (i : Fin n) : RealN n :=
  EuclideanSpace.single i 1

/-- The source set `T = {e₁, …, eₙ}` of standard basis vectors. -/
def standardSimplexVertices (n : ℕ) : Set (RealN n) :=
  Set.range (standardSimplexVertex (n := n))

/-- The barycenter of the standard-basis simplex, with every coordinate `1 / n`. -/
def standardSimplexCenter (n : ℕ) : RealN n :=
  WithLp.toLp 2 (fun _ : Fin n => (n : ℝ)⁻¹)

/--
Source proof: take the uniform weights `1 / n` on the standard basis vectors.
Prover notes: use `Finset.convexHull_eq` for the finite vertex set, show that
these weights are nonnegative and sum to one when `n > 0`, then compute the
resulting coordinate vector as `standardSimplexCenter n`.
-/
theorem standardSimplexCenter_mem_convexHull {n : ℕ} (hn : 0 < n) :
    standardSimplexCenter n ∈ convexHull ℝ (standardSimplexVertices n) := by
  sorry

/--
Source scope: the standard-basis simplex construction is meaningful in positive
natural dimensions. The literal `n = 0` case cannot satisfy the displayed
bound: `RealN 0` has only one point, while the right-hand side is positive for
positive `k`.
Source proof: choose the basis-vertex set and its uniform barycenter.
Prover notes: combine `standardSimplexCenter_mem_convexHull` with the coordinate
calculation used by `standardSimplex_approximateCaratheodory_lower_bound`.
-/
theorem standardSimplex_positive_dimension_witness (n : ℕ) (hn : 0 < n) :
    ∃ T : Set (RealN n), ∃ x : RealN n, x ∈ convexHull ℝ T ∧
      ∀ k : ℕ, 0 < k →
        ∀ (weights : Fin k → ℝ) (points : Fin k → RealN n),
          (∀ j, 0 ≤ weights j) → (∑ j, weights j = 1) →
          (∀ j, points j ∈ T) →
          ‖x - ∑ j, weights j • points j‖ ≥
            Real.sqrt ((1 : ℝ) / k - (1 : ℝ) / n) := by
  sorry

/--
Source proof: each coordinate of a convex combination of basis vectors is the
total weight assigned to that vertex. Its support has cardinality at most `k`.
Prover notes: expand the squared Euclidean distance to the uniform vector;
Cauchy--Schwarz gives squared coordinate norm at least `1 / k`, yielding
`1 / k - 1 / n`, and then apply monotonicity of `Real.sqrt`.
-/
theorem standardSimplex_approximateCaratheodory_lower_bound
    {n k : ℕ} (hn : 0 < n) (hk : 0 < k)
    (weights : Fin k → ℝ) (points : Fin k → RealN n)
    (hweights_nonneg : ∀ j, 0 ≤ weights j)
    (hweights_sum : ∑ j, weights j = 1)
    (hpoints : ∀ j, points j ∈ standardSimplexVertices n) :
    ‖standardSimplexCenter n - ∑ j, weights j • points j‖ ≥
      Real.sqrt ((1 : ℝ) / k - (1 : ℝ) / n) := by
  sorry

/--
Source proof: after fixing `k`, let the dimension tend to infinity in the
displayed lower bound `√(1 / k - 1 / n)`.
Prover notes: prove `(n + 1 : ℝ)⁻¹ → 0` at `atTop`, compose with subtraction,
and use continuity of `Real.sqrt` at the nonnegative limiting value.

This is only the numerical consequence available in the bounded source slice;
the statement of the cited Theorem 0.0.2 is not available here, so this
declaration does not assert an upper-bound comparison or asymptotic tightness.
-/
theorem standardSimplex_displayed_lower_bound_tendsto (k : ℕ) (hk : 0 < k) :
    Filter.Tendsto
      (fun n : ℕ => Real.sqrt ((1 : ℝ) / k - (1 : ℝ) / (n + 1)))
      Filter.atTop (nhds (Real.sqrt ((1 : ℝ) / k))) := by
  sorry

end FateXWork.Questions.Items05784E1F74
