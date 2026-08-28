import Mathlib

namespace FateXWork.Questions.Items066BBAC934

open scoped BigOperators

/-- The real-valued partial binomial sum `∑_{j = 0}^k (n.choose j)`. -/
def binomialPartialSum (n k : ℕ) : ℝ :=
  ∑ j ∈ Finset.range (k + 1), (n.choose j : ℝ)

/-- Mathlib's binomial coefficient transported to integer parameters. Under positive
integer hypotheses, `Int.toNat` is the intended natural-number argument. -/
def intBinomial (n k : ℤ) : ℕ :=
  n.toNat.choose k.toNat

/-- The partial binomial sum for the integer-parameter representation `intBinomial`. -/
def intBinomialPartialSum (n k : ℤ) : ℝ :=
  binomialPartialSum n.toNat k.toNat

private lemma binomial_ratio_le_tail (n k : ℕ) (hk : 1 ≤ k) (hkn : k ≤ n) :
    (n : ℝ) / (k : ℝ) ≤ (n - k + 1 : ℕ) := by
  have hkR : (0 : ℝ) < k := by
    exact_mod_cast (Nat.zero_lt_of_lt hk)
  rw [div_le_iff₀ hkR]
  have hnR : (n : ℝ) = (n - k : ℕ) + k := by
    norm_cast
    omega
  rw [hnR]
  have hnonneg : (0 : ℝ) ≤ (n - k : ℕ) := by positivity
  have hkone : (1 : ℝ) ≤ k := by exact_mod_cast hk
  push_cast
  nlinarith [mul_nonneg hnonneg (sub_nonneg.mpr hkone)]

private lemma choose_step (n k : ℕ) (hkn : k + 2 ≤ n) :
    n.choose (k + 2) * (k + 2) = n * (n - 1).choose (k + 1) := by
  have hsymm : n.choose (n - (k + 2)) = n.choose (k + 2) :=
    Nat.choose_symm hkn
  have hsymm' : (n - 1).choose ((n - 1) - (k + 1)) = (n - 1).choose (k + 1) :=
    Nat.choose_symm (by omega)
  have h := Nat.choose_mul_succ_eq (n - 1) (n - (k + 2))
  rw [show n - (k + 2) = (n - 1) - (k + 1) by omega, hsymm'] at h
  rw [show n - 1 + 1 = n by omega] at h
  rw [show (n - 1) - (k + 1) = n - (k + 2) by omega, hsymm] at h
  rw [show n - (n - (k + 2)) = k + 2 by omega] at h
  simpa [Nat.mul_comm] using h.symm

/-- Source proof: express the binomial coefficient as a product of `k` fractions and
bound each by `n / k`. Prover notes: use a factorial/product formula for `Nat.choose`
and multiply the resulting nonnegative real inequalities. -/
lemma binomial_lower_bound (n k : ℕ) (hk : 1 ≤ k) (hkn : k ≤ n) :
    ((n : ℝ) / (k : ℝ)) ^ k ≤ (n.choose k : ℝ) := by
  induction k generalizing n with
  | zero => omega
  | succ k ih =>
    cases k with
    | zero =>
      simp
    | succ k =>
      have hkn' : k + 1 ≤ n - 1 := by omega
      have ih' := ih (n - 1) (by omega) hkn'
      have hrec := choose_step n k hkn
      have hxy : (n : ℝ) / (k + 2 : ℕ) ≤ ((n - 1 : ℕ) : ℝ) / (k + 1 : ℕ) := by
        rw [Nat.cast_sub (by omega : 1 ≤ n)]
        apply (div_le_div_iff₀ (by positivity) (by positivity)).2
        push_cast
        have hknR : (k + 2 : ℝ) ≤ n := by exact_mod_cast hkn
        nlinarith
      have hpow : ((n : ℝ) / (k + 2 : ℕ)) ^ (k + 1) ≤
          (n - 1).choose (k + 1) := by
        calc
          ((n : ℝ) / (k + 2 : ℕ)) ^ (k + 1) ≤
              (((n - 1 : ℕ) : ℝ) / (k + 1 : ℕ)) ^ (k + 1) := by gcongr
          _ ≤ (n - 1).choose (k + 1) := ih'
      have hxnonneg : (0 : ℝ) ≤ (n : ℝ) / (k + 2 : ℕ) := by positivity
      have hmult : ((n : ℝ) / (k + 2 : ℕ)) ^ (k + 1) *
          ((n : ℝ) / (k + 2 : ℕ)) ≤
          (n - 1).choose (k + 1) * ((n : ℝ) / (k + 2 : ℕ)) := by
        exact mul_le_mul_of_nonneg_right hpow hxnonneg
      have hrecR : (n.choose (k + 2) : ℝ) * (k + 2 : ℕ) =
          (n : ℝ) * ((n - 1).choose (k + 1) : ℝ) := by
        exact_mod_cast hrec
      have hdenpos : (0 : ℝ) < (k + 2 : ℕ) := by positivity
      have hident : ((n - 1).choose (k + 1) : ℝ) *
          ((n : ℝ) / (k + 2 : ℕ)) = (n.choose (k + 2) : ℝ) := by
        calc
          ((n - 1).choose (k + 1) : ℝ) * ((n : ℝ) / (k + 2 : ℕ)) =
              (((n - 1).choose (k + 1) : ℝ) * n) / (k + 2 : ℕ) := by ring
          _ = (n.choose (k + 2) : ℝ) := by
            rw [show ((n - 1).choose (k + 1) : ℝ) * n =
                (n.choose (k + 2) : ℝ) * (k + 2 : ℕ) by nlinarith [hrecR]]
            exact mul_div_cancel_right₀ _ hdenpos.ne'
      calc
        ((n : ℝ) / (k + 2 : ℕ)) ^ (k + 2) =
            ((n : ℝ) / (k + 2 : ℕ)) ^ (k + 1) * ((n : ℝ) / (k + 2 : ℕ)) := by
          rw [show k + 2 = (k + 1) + 1 by omega, pow_succ]
        _ ≤ (n - 1).choose (k + 1) * ((n : ℝ) / (k + 2 : ℕ)) := hmult
        _ = (n.choose (k + 2) : ℝ) := hident

/-- Source proof: the `j = k` summand is present in the displayed partial sum.
Prover notes: show that `k ∈ Finset.range (k + 1)` and use nonnegativity of the
remaining natural binomial summands. -/
lemma choose_le_binomialPartialSum (n k : ℕ) :
    (n.choose k : ℝ) ≤ binomialPartialSum n k := by
  unfold binomialPartialSum; apply Finset.single_le_sum (fun i hi => ?_) (Finset.mem_range.2 (Nat.lt_succ_self k)); positivity

private lemma binomialPartialSum_sum_bound (n k : ℕ) (hk : 1 ≤ k) (hkn : k ≤ n) :
    (∑ j ∈ Finset.range (k + 1), (n.choose j : ℝ)) ≤
      ((n : ℝ) / (k : ℝ)) ^ k * Real.exp (k : ℝ) := by
  have hkR : (0 : ℝ) < k := by
    exact_mod_cast (Nat.zero_lt_of_lt hk)
  have hqone : (1 : ℝ) ≤ (n : ℝ) / (k : ℝ) := by
    rw [one_le_div hkR]
    exact_mod_cast hkn
  have hsumexp := Real.sum_le_exp_of_nonneg (show (0 : ℝ) ≤ k by positivity) (k + 1)
  calc
    ∑ j ∈ Finset.range (k + 1), (n.choose j : ℝ) ≤
        ∑ j ∈ Finset.range (k + 1), ((n : ℝ) ^ j / (j.factorial : ℝ)) := by
      apply Finset.sum_le_sum
      intro j hj
      exact Nat.choose_le_pow_div j n
    _ ≤ ∑ j ∈ Finset.range (k + 1),
        ((n : ℝ) / (k : ℝ)) ^ k * ((k : ℝ) ^ j / (j.factorial : ℝ)) := by
      apply Finset.sum_le_sum
      intro j hj
      have hjk : j ≤ k := Nat.le_of_lt_succ (Finset.mem_range.mp hj)
      have hpow : ((n : ℝ) / (k : ℝ)) ^ j ≤ ((n : ℝ) / (k : ℝ)) ^ k := by
        exact pow_le_pow_right₀ hqone hjk
      calc
        (n : ℝ) ^ j / (j.factorial : ℝ) =
            ((n : ℝ) / (k : ℝ)) ^ j * ((k : ℝ) ^ j / (j.factorial : ℝ)) := by
              rw [div_pow]
              field_simp [hkR.ne']
        _ ≤ ((n : ℝ) / (k : ℝ)) ^ k * ((k : ℝ) ^ j / (j.factorial : ℝ)) := by
              exact mul_le_mul_of_nonneg_right hpow (by positivity)
    _ = ((n : ℝ) / (k : ℝ)) ^ k *
        (∑ j ∈ Finset.range (k + 1), ((k : ℝ) ^ j / (j.factorial : ℝ))) := by
      rw [Finset.mul_sum]
    _ ≤ ((n : ℝ) / (k : ℝ)) ^ k * Real.exp (k : ℝ) := by
      exact mul_le_mul_of_nonneg_left hsumexp (by positivity)

private lemma binomialPartialSum_scale_exp_eq (n k : ℕ) (hk : 1 ≤ k) :
    ((n : ℝ) / (k : ℝ)) ^ k * Real.exp (k : ℝ) =
      ((Real.exp 1 * (n : ℝ)) / (k : ℝ)) ^ k := by
  have hkR : (k : ℝ) ≠ 0 := by
    positivity
  have hexp : Real.exp (k : ℝ) = (Real.exp 1) ^ k := by
    rw [← Real.exp_nat_mul]
    simp
  rw [hexp, ← mul_pow]
  congr 1
  field_simp [hkR]

/-- Source proof: multiply the sum by `(k / n)^k`, replace this on the `j`th summand
by `(k / n)^j`, and use the binomial theorem. Prover notes: derive the standard
exponential estimate to obtain the factor `Real.exp 1`. -/
lemma binomialPartialSum_upper_bound (n k : ℕ) (hk : 1 ≤ k) (hkn : k ≤ n) :
    binomialPartialSum n k ≤ ((Real.exp 1 * (n : ℝ)) / (k : ℝ)) ^ k := by
  have hsum := binomialPartialSum_sum_bound n k hk hkn
  unfold binomialPartialSum
  calc
    ∑ j ∈ Finset.range (k + 1), (n.choose j : ℝ) ≤
        ((n : ℝ) / (k : ℝ)) ^ k * Real.exp (k : ℝ) := hsum
    _ = ((Real.exp 1 * (n : ℝ)) / (k : ℝ)) ^ k :=
      binomialPartialSum_scale_exp_eq n k hk

/-- Source proof: combine the product lower bound, the inclusion of the `k`th
summand, and the binomial-theorem upper bound. Prover notes: apply the three
component lemmas without changing the real coercions or the finite-sum encoding. -/
theorem binomial_coefficient_bounds (n k : ℕ) (hk : 1 ≤ k) (hkn : k ≤ n) :
    ((n : ℝ) / (k : ℝ)) ^ k ≤ (n.choose k : ℝ) ∧
      (n.choose k : ℝ) ≤ binomialPartialSum n k ∧
        binomialPartialSum n k ≤ ((Real.exp 1 * (n : ℝ)) / (k : ℝ)) ^ k := by
  sorry

/-- Source proof: the source states the result for integers `1 ≤ k ≤ n`; use the
explicit `Int.toNat` representation bridge and the native natural-number chain.
Prover notes: simplify `Int.toNat` and real casts using positivity, then apply
`binomial_coefficient_bounds`. -/
theorem integer_binomial_coefficient_bounds (n k : ℤ) (hk : 1 ≤ k) (hkn : k ≤ n) :
    ((n : ℝ) / (k : ℝ)) ^ k.toNat ≤ (intBinomial n k : ℝ) ∧
      (intBinomial n k : ℝ) ≤ intBinomialPartialSum n k ∧
        intBinomialPartialSum n k ≤ ((Real.exp 1 * (n : ℝ)) / (k : ℝ)) ^ k.toNat := by
  sorry

end FateXWork.Questions.Items066BBAC934
