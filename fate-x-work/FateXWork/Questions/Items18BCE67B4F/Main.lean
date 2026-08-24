import Mathlib.Probability.Distributions.SetBernoulli
import Mathlib.Analysis.SpecialFunctions.Log.Base
import Mathlib.Data.Finset.Powerset

abbrev StudentPair (n : ℕ) := { e : Finset (Fin n) // e.card = 2 }

theorem random_graph_no_large_independent_set
    (n : ℕ) (hn : 7 ≤ n)
    {Ω : Type*} [MeasurableSpace Ω]
    (μ : MeasureTheory.Measure Ω) [MeasureTheory.IsProbabilityMeasure μ]
    (edges : Ω → Set (StudentPair n))
    (p : unitInterval)
    (hp : (p : ℝ) = 1 / 2)
    (hedges : ProbabilityTheory.IsSetBernoulli edges Set.univ p μ)
    (hlarge_measurable :
      MeasurableSet {ω |
        ¬ ∃ S : Finset (Fin n),
          (2 : ℝ) * Real.logb 2 (n : ℝ) < (S.card : ℝ) ∧
            ∀ e : StudentPair n, e.1 ⊆ S → e ∉ edges ω}) :
    μ {ω |
      ¬ ∃ S : Finset (Fin n),
        (2 : ℝ) * Real.logb 2 (n : ℝ) < (S.card : ℝ) ∧
          ∀ e : StudentPair n, e.1 ⊆ S → e ∉ edges ω} ≥
      ENNReal.ofReal (1 - 1 / (n : ℝ)) := by sorry
