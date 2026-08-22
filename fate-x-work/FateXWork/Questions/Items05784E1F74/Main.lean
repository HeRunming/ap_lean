import Mathlib

open scoped BigOperators

/-- Approximate Caratheodory lower bound example in Euclidean space: for every positive
ambient dimension `n`, there is a set `T ⊆ ℝ^n` and a point `x ∈ conv(T)` such that every
convex combination of `k` points of `T`, in the meaningful range `0 < k ≤ n`, is at
Euclidean distance at least `sqrt (1 / k - 1 / n)` from `x`. -/
theorem approximateCaratheodory_asymptotically_tight_example :
    ∀ (n : ℕ), 0 < n →
      ∃ (T : Set (EuclideanSpace ℝ (Fin n))) (x : EuclideanSpace ℝ (Fin n)),
        x ∈ convexHull ℝ T ∧
          ∀ (k : ℕ), 0 < k → k ≤ n →
            ∀ (coeff : Fin k → ℝ),
              (∀ j : Fin k, 0 ≤ coeff j) →
              (∑ j : Fin k, coeff j) = 1 →
              ∀ (y : Fin k → EuclideanSpace ℝ (Fin n)),
                (∀ j : Fin k, y j ∈ T) →
                  Real.sqrt ((1 : ℝ) / (k : ℝ) - (1 : ℝ) / (n : ℝ)) ≤
                    ‖x - ∑ j : Fin k, coeff j • y j‖ := by
  sorry

/-- Scalar asymptotic observation corresponding to letting the ambient dimension tend to
infinity while the number `k` of selected points is fixed. -/
theorem approximateCaratheodory_lowerBound_asymptotic_scalar :
    ∀ (k : ℕ), 0 < k →
      Filter.Tendsto
        (fun n : ℕ => Real.sqrt ((1 : ℝ) / (k : ℝ) - (1 : ℝ) / (n : ℝ)))
        Filter.atTop
        (nhds (Real.sqrt ((1 : ℝ) / (k : ℝ)))) := by
  sorry
