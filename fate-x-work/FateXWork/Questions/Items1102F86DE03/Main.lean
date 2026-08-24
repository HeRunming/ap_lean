import Mathlib.Probability.Moments.Variance
import Mathlib.Analysis.SpecialFunctions.Log.Basic
import Mathlib.Analysis.SpecialFunctions.Pow.Asymptotics

open scoped BigOperators ENNReal
open Filter MeasureTheory

theorem sparse_random_graphs_have_isolated_vertices
    {Ω : Type*} [MeasurableSpace Ω]
    (m : ℕ → Measure Ω)
    [∀ n : ℕ, IsProbabilityMeasure (m n)]
    (p : ℕ → ℝ)
    (ε : ℝ)
    (A : ∀ n : ℕ, Fin n → Set Ω)
    (X : ∀ n : ℕ, Fin n → Ω → ℝ)
    (S : ℕ → Ω → ℝ)
    (hε : 0 < ε)
    (hp : ∀ n : ℕ, 0 ≤ p n ∧ p n ≤ 1)
    (hbound : ∀ᶠ n in atTop,
      p n < ((1 - ε) * Real.log (n : ℝ)) / (n : ℝ))
    (hA_measurable : ∀ (n : ℕ) (i : Fin n), MeasurableSet (A n i))
    (hS : ∀ n : ℕ,
      S n = fun ω => ∑ i : Fin n, X n i ω)
    (hX : ∀ (n : ℕ) (i : Fin n),
      X n i = Set.indicator (A n i) (fun _ => (1 : ℝ)))
    (h_single : ∀ n : ℕ, 1 ≤ n → ∀ i : Fin n,
      m n (A n i) = ENNReal.ofReal ((1 - p n) ^ (n - 1)))
    (h_pair : ∀ (n : ℕ) (i j : Fin n), 1 ≤ n → i ≠ j →
      m n (A n i ∩ A n j) = ENNReal.ofReal ((1 - p n) ^ (2 * n - 3))) :
    Filter.Tendsto
        (fun n : ℕ => ∫ ω, S n ω ∂m n)
        Filter.atTop Filter.atTop
      ∧ Filter.Tendsto
          (fun n : ℕ =>
            ProbabilityTheory.variance (S n) (m n) /
              (∫ ω, S n ω ∂m n) ^ 2)
          Filter.atTop (nhds 0)
      ∧ Filter.Tendsto
          (fun n : ℕ => m n {ω | ∃ i : Fin n, ω ∈ A n i})
          Filter.atTop (nhds (1 : ENNReal)) := by
  sorry
