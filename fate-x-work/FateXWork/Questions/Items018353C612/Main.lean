import FateXWork.Questions.Shared.Probability

noncomputable section

open scoped BigOperators
open MeasureTheory ProbabilityTheory
open FateXWork.Questions.Shared

namespace FateXWork.Questions.Items018353C612

/--
Source proof: the reference solution recalls the Euclidean expansion
`‖x - y‖₂² = ‖x‖₂² - 2⟪x, y⟫ + ‖y‖₂²`, obtained by expanding
`⟪x - y, x - y⟫`.
Prover notes: unfold the squared norm through the real inner product, use
bilinearity and symmetry of `inner ℝ`, and simplify the resulting polynomial
identity.
-/
lemma norm_sub_sq_realN {n : ℕ} (x y : RealN n) :
    ‖x - y‖ ^ 2 = ‖x‖ ^ 2 - 2 * inner ℝ x y + ‖y‖ ^ 2 := by
  exact norm_sub_sq_real x y

private theorem integrable_inner_const_realN
    {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω}
    {n : ℕ} (Z : Ω → RealN n) (hZ : Integrable Z μ) (m : RealN n) :
    Integrable (fun ω => inner ℝ (Z ω) m) μ := by
  have hcoord (i : Fin n) : Integrable (fun ω => Z ω i) μ :=
    (EuclideanSpace.proj (𝕜 := ℝ) i).integrable_comp hZ
  rw [show (fun ω => inner ℝ (Z ω) m) = fun ω => ∑ i : Fin n, Z ω i * m i by
    funext ω
    simp [PiLp.inner_apply, RCLike.inner_apply, mul_comm]]
  induction (Finset.univ : Finset (Fin n)) using Finset.induction_on with
  | empty => simp
  | @insert i s hi ih =>
      simpa [Finset.sum_insert hi] using (hcoord i).mul_const (m i) |>.add ih

private theorem integral_inner_const_realN
    {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω}
    {n : ℕ} (Z : Ω → RealN n) (hZ : Integrable Z μ) (m : RealN n) :
    (∫ ω, inner ℝ (Z ω) m ∂μ) = inner ℝ (∫ ω, Z ω ∂μ) m := by
  have hcoord (i : Fin n) : Integrable (fun ω => Z ω i) μ :=
    (EuclideanSpace.proj (𝕜 := ℝ) i).integrable_comp hZ
  calc
    (∫ ω, inner ℝ (Z ω) m ∂μ) = ∫ ω, ∑ i : Fin n, Z ω i * m i ∂μ := by
      apply integral_congr_ae
      filter_upwards [] with ω
      simp [PiLp.inner_apply, RCLike.inner_apply, mul_comm]
    _ = ∑ i : Fin n, ∫ ω, Z ω i * m i ∂μ := by
      rw [integral_finset_sum]
      intro i _
      exact (hcoord i).mul_const (m i)
    _ = ∑ i : Fin n, (∫ ω, Z ω i ∂μ) * m i := by
      apply Finset.sum_congr rfl
      intro i _
      rw [integral_mul_const]
    _ = inner ℝ (∫ ω, Z ω ∂μ) m := by
      simp only [PiLp.inner_apply, RCLike.inner_apply, conj_trivial]
      apply Finset.sum_congr rfl
      intro i _
      have hx := (EuclideanSpace.proj (𝕜 := ℝ) i).integral_comp_comm hZ
      change (∫ ω, Z ω i ∂μ) = (∫ ω, Z ω ∂μ) i at hx
      rw [hx]
      ring

/--
Source proof: apply the preceding expansion pointwise with
`y = 𝔼 Z = ∫ η, Z η ∂μ`, then integrate. The cross term integrates to the
inner product of the mean with itself, and the integral of the constant
`‖𝔼 Z‖²` is that constant because `μ` is a probability measure.
Prover notes: use `norm_sub_sq_realN`, Bochner integral linearity for real
integrands, and `integral_const`/`measure_univ` simplifications for probability
measures.
-/
theorem variance_identity_realN
    {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω} [IsProbabilityMeasure μ]
    {n : ℕ} (Z : Ω → RealN n)
    (hZ : Integrable Z μ)
    (hZ_sq : Integrable (fun ω => ‖Z ω‖ ^ 2) μ) :
    (∫ ω, ‖Z ω - (∫ η, Z η ∂μ)‖ ^ 2 ∂μ)
      = (∫ ω, ‖Z ω‖ ^ 2 ∂μ) - ‖(∫ η, Z η ∂μ)‖ ^ 2 := by
  let m : RealN n := ∫ η, Z η ∂μ
  have h_inner : Integrable (fun ω => inner ℝ (Z ω) m) μ :=
    integrable_inner_const_realN Z hZ m
  have h_two_inner : Integrable (fun ω => 2 * inner ℝ (Z ω) m) μ :=
    h_inner.const_mul 2
  have h_const : Integrable (fun _ : Ω => ‖m‖ ^ 2) μ := integrable_const _
  have h_expand : (fun ω => ‖Z ω - m‖ ^ 2)
      = (fun ω => ‖Z ω‖ ^ 2 - 2 * inner ℝ (Z ω) m + ‖m‖ ^ 2) := by
    funext ω
    exact norm_sub_sq_realN (Z ω) m
  have h_int_inner : (∫ ω, inner ℝ (Z ω) m ∂μ) = inner ℝ m m := by
    simpa [m] using integral_inner_const_realN Z hZ m
  calc
    (∫ ω, ‖Z ω - (∫ η, Z η ∂μ)‖ ^ 2 ∂μ)
        = ∫ ω, ‖Z ω - m‖ ^ 2 ∂μ := by simp [m]
    _ = ∫ ω, ‖Z ω‖ ^ 2 - 2 * inner ℝ (Z ω) m + ‖m‖ ^ 2 ∂μ := by
      rw [h_expand]
    _ = (∫ ω, ‖Z ω‖ ^ 2 - 2 * inner ℝ (Z ω) m ∂μ)
          + ∫ ω, ‖m‖ ^ 2 ∂μ := by
      simpa only [Pi.add_apply, Pi.sub_apply] using
        (integral_add (hZ_sq.sub h_two_inner) h_const)
    _ = ((∫ ω, ‖Z ω‖ ^ 2 ∂μ) - (∫ ω, 2 * inner ℝ (Z ω) m ∂μ))
          + ∫ ω, ‖m‖ ^ 2 ∂μ := by
      rw [integral_sub hZ_sq h_two_inner]
    _ = ((∫ ω, ‖Z ω‖ ^ 2 ∂μ) - 2 * (∫ ω, inner ℝ (Z ω) m ∂μ))
          + ‖m‖ ^ 2 := by
      rw [integral_const_mul, integral_const]
      simp
    _ = ((∫ ω, ‖Z ω‖ ^ 2 ∂μ) - 2 * inner ℝ m m) + ‖m‖ ^ 2 := by
      rw [h_int_inner]
    _ = (∫ ω, ‖Z ω‖ ^ 2 ∂μ) - ‖m‖ ^ 2 := by
      have hm : inner ℝ m m = ‖m‖ ^ 2 := by
        simp
      rw [hm]
      ring
    _ = (∫ ω, ‖Z ω‖ ^ 2 ∂μ) - ‖(∫ η, Z η ∂μ)‖ ^ 2 := by
      simp [m]

/--
No separate source proof text for part (b) is available beyond the item 0.1
Euclidean expansion hint.
Derived strategy: expand `‖Z - Z'‖₂²`, integrate, use that `Z'` has the same
distribution as `Z` to identify the two second moments and the two means, and
use independence to factor the mixed inner-product term. This makes
`𝔼 ‖Z - Z'‖²` equal to twice the variance expression from part (a).
Prover notes: combine `variance_identity_realN`, `IdentDistrib.integral_eq`
for the equal-distribution transfers, and the shared lemma
`integral_inner_eq_inner_integral_of_indepFun hZ hZcopy h_indep` for the
cross term.
-/
theorem variance_identity_independent_copy_realN
    {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω} [IsProbabilityMeasure μ]
    {n : ℕ} (Z Zcopy : Ω → RealN n)
    (hZ : Integrable Z μ) (hZcopy : Integrable Zcopy μ)
    (hZ_sq : Integrable (fun ω => ‖Z ω‖ ^ 2) μ)
    (hZcopy_sq : Integrable (fun ω => ‖Zcopy ω‖ ^ 2) μ)
    (h_indep : IndepFun Z Zcopy μ)
    (h_ident : IdentDistrib Z Zcopy μ μ) :
    (∫ ω, ‖Z ω - (∫ η, Z η ∂μ)‖ ^ 2 ∂μ)
      = (1 / 2 : ℝ) * (∫ ω, ‖Z ω - Zcopy ω‖ ^ 2 ∂μ) := by
  sorry

end FateXWork.Questions.Items018353C612
