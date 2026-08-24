import Mathlib

open scoped ENNReal
open Set
open Metric
open MeasureTheory

/-- Carl--Pajor (KKK) volume estimate for an `N`-vertex polytope in the unit Euclidean ball. -/
theorem carl_pajor_volume_bound :
    ∃ C : ℝ, 0 < C ∧
      ∀ (n N : ℕ) (P : Set (EuclideanSpace ℝ (Fin n)))
        (v : Fin N → EuclideanSpace ℝ (Fin n)),
        0 < n →
        n ≤ N →
        Function.Injective v →
        P = convexHull ℝ (Set.range v) →
        Set.extremePoints ℝ P = Set.range v →
        P ⊆ Metric.closedBall (0 : EuclideanSpace ℝ (Fin n)) 1 →
        MeasureTheory.volume P /
            MeasureTheory.volume
              (Metric.closedBall (0 : EuclideanSpace ℝ (Fin n)) 1) ≤
          (ENNReal.ofReal
            (C * Real.sqrt
              (Real.log (Real.exp 1 * (N : ℝ) / (n : ℝ)) / (n : ℝ)))) ^ n := by
  sorry
