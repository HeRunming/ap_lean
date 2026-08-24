import Mathlib.Analysis.Convex.Combination
import Mathlib.Data.Real.Basic

open Set

theorem cube_eq_convexHull_vertices (n : ℕ) :
    Set.univ.pi (fun _ : Fin n => Set.Icc (-1 : ℝ) 1) =
      convexHull ℝ (Set.univ.pi (fun _ : Fin n => ({(-1 : ℝ), 1} : Set ℝ))) := by
  sorry
