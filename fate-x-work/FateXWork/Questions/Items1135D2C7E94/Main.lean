import Mathlib

open MeasureTheory ProbabilityTheory

/-- Bounds for the expectation of the pointwise maximum of finitely many
nonnegative integrable real-valued random variables. -/
theorem expectation_iSup_bounds
    {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω} [IsProbabilityMeasure μ]
    {n : ℕ} (hn : 0 < n) (X : Fin n → Ω → ℝ)
    (hX_measurable : ∀ i : Fin n, Measurable (X i))
    (hX_nonneg : ∀ i : Fin n, ∀ ω : Ω, 0 ≤ X i ω)
    (hX_integrable : ∀ i : Fin n, Integrable (X i) μ) :
    (⨆ i : Fin n, ∫ ω, X i ω ∂μ) ≤
        ∫ ω, ⨆ i : Fin n, X i ω ∂μ ∧
      (∫ ω, ⨆ i : Fin n, X i ω ∂μ) ≤
        (n : ℝ) * (⨆ i : Fin n, ∫ ω, X i ω ∂μ) := by sorry

/-- Examples witnessing equality in each of the two expectation-of-maximum
bounds. -/
theorem expectation_iSup_optimal_examples
    (n : ℕ) (hn : 0 < n) :
    ∃ (Ω : Type*) (mΩ : MeasurableSpace Ω)
        (μ : @Measure Ω mΩ),
      letI : MeasurableSpace Ω := mΩ
      IsProbabilityMeasure μ ∧
        ∃ (X Y : Fin n → Ω → ℝ),
          (∀ i : Fin n, Measurable (X i)) ∧
          (∀ i : Fin n, Measurable (Y i)) ∧
          (∀ i : Fin n, ∀ ω : Ω, 0 ≤ X i ω) ∧
          (∀ i : Fin n, ∀ ω : Ω, 0 ≤ Y i ω) ∧
          (∀ i : Fin n, Integrable (X i) μ) ∧
          (∀ i : Fin n, Integrable (Y i) μ) ∧
          ((⨆ i : Fin n, ∫ ω, X i ω ∂μ) =
            ∫ ω, ⨆ i : Fin n, X i ω ∂μ) ∧
          0 < (⨆ i : Fin n, ∫ ω, X i ω ∂μ) ∧
          (∫ ω, ⨆ i : Fin n, Y i ω ∂μ) =
            (n : ℝ) * (⨆ i : Fin n, ∫ ω, Y i ω ∂μ) ∧
          0 < (∫ ω, ⨆ i : Fin n, Y i ω ∂μ) := by sorry

/-- Independent nonnegative random variables for which the upper bound is
optimal up to a positive absolute real constant. -/
theorem independent_expectation_iSup_approximately_optimal :
    ∃ coeff : ℝ, 0 < coeff ∧
      ∀ n : ℕ, 0 < n →
        ∃ (Ω : Type*) (mΩ : MeasurableSpace Ω)
            (μ : @Measure Ω mΩ),
          letI : MeasurableSpace Ω := mΩ
          IsProbabilityMeasure μ ∧
            ∃ X : Fin n → Ω → ℝ,
              (∀ i : Fin n, Measurable (X i)) ∧
              (∀ i : Fin n, ∀ ω : Ω, 0 ≤ X i ω) ∧
              (∀ i : Fin n, Integrable (X i) μ) ∧
              iIndepFun X μ ∧
              (∫ ω, ⨆ i : Fin n, X i ω ∂μ) >
                coeff * (n : ℝ) * (⨆ i : Fin n, ∫ ω, X i ω ∂μ) := by sorry
