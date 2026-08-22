import Mathlib

private lemma finite_barycentric_of_mem_convexHull
    {n : ℕ} {T : Set (EuclideanSpace ℝ (Fin n))}
    {x : EuclideanSpace ℝ (Fin n)}
    (hx : x ∈ convexHull ℝ T) :
    ∃ (ι : Type) (_ : Fintype ι)
      (z : ι → EuclideanSpace ℝ (Fin n)) (w : ι → ℝ),
      (∀ i, 0 ≤ w i) ∧
      (∑ i, w i) = 1 ∧
      (∀ i, z i ∈ T) ∧
      x = ∑ i, w i • z i := by
  classical
  obtain ⟨ι, hι, w, z, hw, hsum, hx'⟩ :=
    mem_convexHull_iff_exists_fintype.mp hx
  refine ⟨ι, hι, z, w, hw, hsum, ?_, ?_⟩
  · exact hx'.1
  · exact hx'.2.symm

private lemma nonempty_of_weights_sum_one
    {ι : Type} [Fintype ι] (w : ι → ℝ)
    (hsum : ∑ i, w i = 1) : Nonempty ι := by
  by_contra h
  letI : IsEmpty ι := ⟨fun i => h ⟨i⟩⟩
  simpa using hsum

private lemma norm_le_one_div_sqrt_of_sq_le
    {n : ℕ} (k : ℕ) (hk : 0 < k)
    (u : EuclideanSpace ℝ (Fin n))
    (h : ‖u‖ ^ 2 ≤ 1 / (k : ℝ)) :
    ‖u‖ ≤ 1 / Real.sqrt (k : ℝ) := by
  have hkR : 0 < (k : ℝ) := by exact_mod_cast hk
  have hsqrt : 0 < Real.sqrt (k : ℝ) := Real.sqrt_pos.2 hkR
  have hright : 0 ≤ 1 / Real.sqrt (k : ℝ) := le_of_lt (one_div_pos.mpr hsqrt)
  have hsquare : (1 / Real.sqrt (k : ℝ)) ^ 2 = 1 / (k : ℝ) := by
    norm_num [div_pow, Real.sq_sqrt (le_of_lt hkR)]
  calc
    ‖u‖ = Real.sqrt (‖u‖ ^ 2) := by
      rw [Real.sqrt_sq_eq_abs, abs_of_nonneg (norm_nonneg _)]
    _ ≤ Real.sqrt (1 / (k : ℝ)) := Real.sqrt_le_sqrt h
    _ = Real.sqrt ((1 / Real.sqrt (k : ℝ)) ^ 2) := by rw [hsquare]
    _ = 1 / Real.sqrt (k : ℝ) := by
      rw [Real.sqrt_sq_eq_abs, abs_of_nonneg hright]

private lemma exists_le_of_weighted_sum_le
    {ι : Type} [Fintype ι] (w f : ι → ℝ) (c : ℝ)
    (hw : ∀ i, 0 ≤ w i) (hsum : ∑ i, w i = 1)
    (h : ∑ i, w i * f i ≤ c) :
    ∃ i, f i ≤ c := by
  by_contra hn
  push_neg at hn
  have hpos : ∃ i, 0 < w i := by
    by_contra hnone
    push_neg at hnone
    have hz : ∑ i, w i ≤ 0 := Finset.sum_nonpos fun i _ => hnone i
    linarith
  obtain ⟨i, hi⟩ := hpos
  have hle : ∀ i ∈ Finset.univ, w i * c ≤ w i * f i := by
    intro j hj
    exact mul_le_mul_of_nonneg_left (le_of_lt (hn j)) (hw j)
  have hstrict : w i * c < w i * f i :=
    mul_lt_mul_of_pos_left (hn i) hi
  have hlt : (∑ i, w i * c) < ∑ i, w i * f i :=
    Finset.sum_lt_sum hle ⟨i, Finset.mem_univ _, hstrict⟩
  rw [← Finset.sum_mul, hsum] at hlt
  linarith

private lemma weighted_deviation_sum_zero
    {ι : Type} [Fintype ι] {n : ℕ}
    (z : ι → EuclideanSpace ℝ (Fin n)) (w : ι → ℝ)
    (x : EuclideanSpace ℝ (Fin n))
    (hsum : ∑ i, w i = 1)
    (hx : x = ∑ i, w i • z i) :
    ∑ i, w i • (z i - x) = 0 := by
  calc
    ∑ i, w i • (z i - x)
        = (∑ i, w i • z i) - (∑ i, w i • x) := by
          simp_rw [smul_sub]
          rw [Finset.sum_sub_distrib]
    _ = (∑ i, w i • z i) - (∑ i, w i) • x := by
          rw [Finset.sum_smul]
    _ = x - 1 • x := by
          simp [← hx, hsum]
    _ = 0 := by simp

private lemma sum_pi_weights_eq_one
    {ι κ : Type} [Fintype ι] [Fintype κ] [DecidableEq κ]
    (w : ι → ℝ) (hsum : (∑ i, w i) = 1) :
    (∑ s : κ → ι, ∏ j : κ, w (s j)) = 1 := by
  classical
  rw [← Fintype.prod_sum]
  simp [hsum]

theorem approximate_caratheodory_equal_weights
    (n : ℕ) (T : Set (EuclideanSpace ℝ (Fin n)))
    (hT : ∀ y ∈ T, ‖y‖ ≤ 1) :
    ∀ x : EuclideanSpace ℝ (Fin n), x ∈ convexHull ℝ T →
    ∀ k : ℕ, 0 < k →
    ∃ xs : Fin k → EuclideanSpace ℝ (Fin n),
      (∀ j : Fin k, xs j ∈ T) ∧
      ‖x - ((1 / (k : ℝ)) • ∑ j : Fin k, xs j)‖ ≤ 1 / Real.sqrt (k : ℝ) := by
  classical
  intro x hx k hk
  obtain ⟨ι, hι, z, w, hw, hsum, hz, hxw⟩ :=
    finite_barycentric_of_mem_convexHull hx
  sorry
