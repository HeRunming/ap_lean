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

/-- Source proof: express the binomial coefficient as a product of `k` fractions and
bound each by `n / k`. Prover notes: use a factorial/product formula for `Nat.choose`
and multiply the resulting nonnegative real inequalities. -/
lemma binomial_lower_bound (n k : ℕ) (hk : 1 ≤ k) (hkn : k ≤ n) :
    ((n : ℝ) / (k : ℝ)) ^ k ≤ (n.choose k : ℝ) := by
  sorry

/-- Source proof: the `j = k` summand is present in the displayed partial sum.
Prover notes: show that `k ∈ Finset.range (k + 1)` and use nonnegativity of the
remaining natural binomial summands. -/
lemma choose_le_binomialPartialSum (n k : ℕ) :
    (n.choose k : ℝ) ≤ binomialPartialSum n k := by
  sorry

/-- Source proof: multiply the sum by `(k / n)^k`, replace this on the `j`th summand
by `(k / n)^j`, and use the binomial theorem. Prover notes: derive the standard
exponential estimate to obtain the factor `Real.exp 1`. -/
lemma binomialPartialSum_upper_bound (n k : ℕ) (hk : 1 ≤ k) (hkn : k ≤ n) :
    binomialPartialSum n k ≤ ((Real.exp 1 * (n : ℝ)) / (k : ℝ)) ^ k := by
  sorry

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
