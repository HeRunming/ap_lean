import Mathlib

open Filter Topology MeasureTheory

/-- For an Erdős–Rényi random graph on `Fin n`, if every graph has the product
probability determined by independent edges of probability `p n`, and
`p n > (1 + ε) log n / n` for every positive `n`, then the probability that
all vertices have a friend tends to `1`. -/
theorem dense_random_graphs_no_isolated_vertices
    {Ω : Type*} [MeasurableSpace Ω]
    (μ : Measure Ω) [IsProbabilityMeasure μ]
    (ε : ℝ) (p : ℕ → ℝ)
    (X : ∀ n : ℕ, Ω → SimpleGraph (Fin n))
    (hε : 0 < ε)
    (hp_lower : ∀ n : ℕ, 0 < n →
      (1 + ε) * Real.log (n : ℝ) / (n : ℝ) < p n)
    (hp_nonneg : ∀ n : ℕ, 0 ≤ p n)
    (hp_le_one : ∀ n : ℕ, p n ≤ 1)
    (h_fiber_measurable : ∀ (n : ℕ) (G : SimpleGraph (Fin n)),
      MeasurableSet {ω : Ω | X n ω = G})
    (h_erdos_renyi_law : ∀ (n : ℕ) (G : SimpleGraph (Fin n)),
      μ.real {ω : Ω | X n ω = G} =
        (p n) ^ G.edgeFinset.card *
          (1 - p n) ^ ((⊤ : SimpleGraph (Fin n)).edgeFinset.card - G.edgeFinset.card)) :
    Tendsto
      (fun n : ℕ =>
        μ.real {ω : Ω | ∀ v : Fin n, ∃ w : Fin n, (X n ω).Adj v w})
      atTop
      (nhds 1) := by sorry
