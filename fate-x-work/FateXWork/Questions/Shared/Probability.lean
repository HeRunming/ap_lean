import FateXWork.Questions.Shared.Analysis

/-! Verified Probability definitions and lemmas for this corpus. -/

open MeasureTheory ProbabilityTheory

namespace FateXWork.Questions.Shared

/-- The pointwise inner product of two integrable independent Euclidean random vectors is
integrable. This finite-dimensional bridge is useful throughout the HDP corpus. -/
theorem integrable_inner_of_indepFun
    {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω}
    {n : ℕ} {X Y : Ω → RealN n}
    (hX : Integrable X μ) (hY : Integrable Y μ)
    (h_indep : IndepFun X Y μ) : Integrable (fun ω => inner ℝ (X ω) (Y ω)) μ := by
  have hcoordX (i : Fin n) : Integrable (fun ω => X ω i) μ :=
    (EuclideanSpace.proj (𝕜 := ℝ) i).integrable_comp hX
  have hcoordY (i : Fin n) : Integrable (fun ω => Y ω i) μ :=
    (EuclideanSpace.proj (𝕜 := ℝ) i).integrable_comp hY
  have hcoord (i : Fin n) : Integrable (fun ω => X ω i * Y ω i) μ := by
    have hi : IndepFun (fun ω => X ω i) (fun ω => Y ω i) μ := by
      simpa only [Function.comp_apply] using
        h_indep.comp (EuclideanSpace.proj (𝕜 := ℝ) i).continuous.measurable
          (EuclideanSpace.proj (𝕜 := ℝ) i).continuous.measurable
    exact hi.integrable_mul (hcoordX i) (hcoordY i)
  rw [show (fun ω => inner ℝ (X ω) (Y ω)) =
      fun ω => ∑ i : Fin n, X ω i * Y ω i by
        funext ω
        simp [PiLp.inner_apply, RCLike.inner_apply, mul_comm]]
  induction (Finset.univ : Finset (Fin n)) using Finset.induction_on with
  | empty => simp
  | @insert i s hi ih =>
      simpa [Finset.sum_insert hi] using (hcoord i).add ih

/-- Expectation factorization for the inner product of independent integrable Euclidean
random vectors. Mathlib supplies the scalar product result; this theorem lifts it coordinatewise. -/
theorem integral_inner_eq_inner_integral_of_indepFun
    {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω}
    {n : ℕ} {X Y : Ω → RealN n}
    (hX : Integrable X μ) (hY : Integrable Y μ)
    (h_indep : IndepFun X Y μ) :
    (∫ ω, inner ℝ (X ω) (Y ω) ∂μ) =
      inner ℝ (∫ ω, X ω ∂μ) (∫ ω, Y ω ∂μ) := by
  have hcoordX (i : Fin n) : Integrable (fun ω => X ω i) μ :=
    (EuclideanSpace.proj (𝕜 := ℝ) i).integrable_comp hX
  have hcoordY (i : Fin n) : Integrable (fun ω => Y ω i) μ :=
    (EuclideanSpace.proj (𝕜 := ℝ) i).integrable_comp hY
  have hcoord_indep (i : Fin n) : IndepFun (fun ω => X ω i) (fun ω => Y ω i) μ := by
    simpa only [Function.comp_apply] using
      h_indep.comp (EuclideanSpace.proj (𝕜 := ℝ) i).continuous.measurable
        (EuclideanSpace.proj (𝕜 := ℝ) i).continuous.measurable
  calc
    (∫ ω, inner ℝ (X ω) (Y ω) ∂μ) =
        ∫ ω, ∑ i : Fin n, X ω i * Y ω i ∂μ := by
          apply integral_congr_ae
          filter_upwards [] with ω
          simp [PiLp.inner_apply, RCLike.inner_apply, mul_comm]
    _ = ∑ i : Fin n, ∫ ω, X ω i * Y ω i ∂μ := by
          rw [integral_finset_sum]
          intro i _
          exact (hcoord_indep i).integrable_mul (hcoordX i) (hcoordY i)
    _ = ∑ i : Fin n, (∫ ω, X ω i ∂μ) * (∫ ω, Y ω i ∂μ) := by
          apply Finset.sum_congr rfl
          intro i _
          simpa only [Pi.mul_apply] using
            (hcoord_indep i).integral_mul_eq_mul_integral
              (hcoordX i).aestronglyMeasurable (hcoordY i).aestronglyMeasurable
    _ = inner ℝ (∫ ω, X ω ∂μ) (∫ ω, Y ω ∂μ) := by
          simp only [PiLp.inner_apply, RCLike.inner_apply, conj_trivial]
          apply Finset.sum_congr rfl
          intro i _
          have hx := (EuclideanSpace.proj (𝕜 := ℝ) i).integral_comp_comm hX
          have hy := (EuclideanSpace.proj (𝕜 := ℝ) i).integral_comp_comm hY
          change (∫ ω, X ω i ∂μ) = (∫ ω, X ω ∂μ) i at hx
          change (∫ ω, Y ω i ∂μ) = (∫ ω, Y ω ∂μ) i at hy
          rw [hx, hy]
          ring

end FateXWork.Questions.Shared
