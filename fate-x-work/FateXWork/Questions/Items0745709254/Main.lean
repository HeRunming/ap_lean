import Mathlib.MeasureTheory.Measure.Lebesgue.VolumeOfBalls
import Mathlib.Analysis.Complex.Exponential

open MeasureTheory

theorem thinShellPhenomenon (n : ℕ) (hn : 0 < n) :
    ((99 : ENNReal) / 100) *
        MeasureTheory.volume (Metric.closedBall (0 : EuclideanSpace ℝ (Fin n)) 1) <
      MeasureTheory.volume
        ({x : EuclideanSpace ℝ (Fin n) |
          x ∈ Metric.closedBall (0 : EuclideanSpace ℝ (Fin n)) 1 ∧
            1 - 5 / (n : ℝ) ≤ ‖x‖}) := by
  sorry
