# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Scoped source slice: `items-0.6`, extracted at `.leanflow/workflow-state/formalization/HDP-source-full-qa-questions/batches/items-0.6/extracted.txt`
- Target Lean entry file: `FateXWork/Questions/Items066BBAC934/Main.lean`
- Status: declaration draft prepared; awaiting independent statement/source verification.

## Planner Checklist

- [x] Identify definitions and notation required before the theorem statements.
- [x] Split the displayed chain into lower, middle, and upper component lemmas plus one source-facing theorem.
- [x] Record the source locator and all available source proof text.
- [x] Check local project and Mathlib names before introducing declarations.
- [x] Compare the drafted Lean statements with the bounded source slice.
- [ ] Run independent statement/source verification review and apply any corrections.
- [x] Record the complete available source proof text and its limitation.
- [x] Record source-aware proof strategies for every proof obligation.
- [x] Confirm that the draft has no definition, structure, class, or instance construction stubs.
- [ ] Mark theorem and lemma `sorry` declarations ready for a user-started prove workflow. (Reserved for the independent review.)

## Import Plan

Direct Lean imports in `Main.lean`:

- `Mathlib`

## Suggested Search Modules

Non-gating modules or namespaces to search during a later proof workflow. They are not direct imports in the draft.

- `Mathlib.Data.Nat.Choose.Basic` for `Nat.choose_eq_factorial_div_factorial` and binomial identities
- `Mathlib.Data.Nat.Choose.Bounds` for existing elementary binomial bounds
- `Mathlib.Algebra.BigOperators` for finite sums and the binomial theorem
- `Mathlib.Analysis.SpecialFunctions.Log.Basic` for the analytic estimate behind the `e` bound

## Generated File Layout

- Single source-aligned entry module: `FateXWork/Questions/Items066BBAC934/Main.lean`.
- Wrapper module `FateXWork/Questions/Items066BBAC934.lean` imports the entry module.
- Root module coverage is already present: `FateXWork.lean` imports `FateXWork.Questions.Items066BBAC934`.
- No split is planned: one source item, its representation bridge, and its three inequalities fit coherently in `Main.lean`.

## Definitions and Representation

- `binomialPartialSum n k : ℝ` denotes `∑ j = 0, …, k, (n.choose j : ℝ)`, implemented with `Finset.range (k + 1)`.
- Mathlib's binomial coefficient is `Nat.choose`. To preserve the source's stated positive-integer parameter domain, `intBinomial n k` is the explicit bridge `n.toNat.choose k.toNat` and `intBinomialPartialSum n k` is its finite partial sum.
- In the source-facing theorem, the hypotheses `1 ≤ k ≤ n` ensure that `n` and `k` are nonnegative, so `Int.toNat` agrees with the intended positive-integer inputs. Lean's natural exponent `k.toNat` is therefore the source exponent.
- `Real.exp 1` represents Euler's number `e`; all displayed quantities are real-valued after coercing binomial coefficients.

## Source Statement Inventory

### 0.6

- Label: Bounds on binomial coefficients.
- Kind: question.
- Source locator: `HDP/source/full/qa/questions.json`, scoped entry `[0.6]`, lines 1–7 of `.leanflow/workflow-state/formalization/HDP-source-full-qa-questions/batches/items-0.6/extracted.txt`.
- Planned Lean declarations:
  - `binomialPartialSum` — implemented finite-sum representation.
  - `intBinomial` and `intBinomialPartialSum` — implemented positive-integer representation bridge.
  - `binomial_lower_bound` — the first displayed inequality on natural-number inputs.
  - `choose_le_binomialPartialSum` — the middle displayed inequality on natural-number inputs.
  - `binomialPartialSum_upper_bound` — the final displayed inequality on natural-number inputs.
  - `binomial_coefficient_bounds` — conjunction of the three inequalities on natural-number inputs.
  - `integer_binomial_coefficient_bounds` — source-facing positive-integer form, transported through the explicit bridge.
- Dependencies:
  - `Nat.choose`, real coercions, `Finset.range`, finite sums, and natural powers from Mathlib;
  - `Real.exp 1` for Euler's number;
  - later proofs should use the factorial/product formula for the lower bound and the binomial theorem plus the usual exponential estimate for the upper bound;
  - `integer_binomial_coefficient_bounds` should follow from `binomial_coefficient_bounds` after simplifying positive `Int.toNat` coercions.
- Formal statement review: `integer_binomial_coefficient_bounds` retains the source's integer variables and hypotheses, while the implemented `Int.toNat` bridge makes Mathlib's natural binomial coefficient and exponent representation explicit.
- Source proof / prover notes: the complete bounded reference hint and component-by-component proof plan are recorded below; the later proof should use the factorial/product lower bound and the source's binomial-theorem upper-bound argument.

#### Formal statement review

The source quantifies over integers `n, k` satisfying `1 ≤ k ≤ n` and displays a chain of three real inequalities. The source-facing declaration `integer_binomial_coefficient_bounds` has exactly those integer hypotheses. Since Mathlib's standard binomial coefficient is `Nat.choose`, the fully implemented definitions `intBinomial` and `intBinomialPartialSum` explicitly bridge the positive-integer inputs to its natural-number arguments. The hypotheses make that bridge faithful. The source sum from `j = 0` through `k` is `Finset.range (k.toNat + 1)`; all binomial coefficients and divisions are coerced to `ℝ`; `Real.exp 1` is the standard formal representative of `e`; and the natural power exponent `k.toNat` agrees with the positive source integer `k`.

The core natural-number declarations state the same components in Mathlib's native representation. `binomial_coefficient_bounds` is the exact conjunction encoding of the displayed chained inequalities and is the dependency used to prove the source-facing integer theorem. No construction has a placeholder.

- Source qualifiers:
  - parameters are integers with `1 ≤ k ≤ n`;
  - the binomial coefficient is `\binom{n}{k}`;
  - the lower term is `(n / k)^k`;
  - the middle term is the finite sum `∑_{j=0}^k \binom{n}{j}`;
  - the upper term is `(e n / k)^k`;
  - all three inequalities are asserted in one chain.
- Lean coverage:
  - `integer_binomial_coefficient_bounds` retains the integer quantifier order and both order hypotheses;
  - `intBinomial` and `intBinomialPartialSum` record the representation bridge to Mathlib's `Nat.choose` rather than silently changing domains;
  - coercions to `ℝ`, `Real.exp 1`, and `k.toNat` respectively formalize the source's real divisions, Euler constant, and positive integer exponent;
  - its conjunction has exactly the lower, middle, and upper comparisons from the displayed chain.
- Scope changes:
  - None intended. The source leaves the convention for binomial coefficients at positive integer arguments implicit; the explicit `Int.toNat` bridge fixes it to Mathlib's standard `Nat.choose` convention, which agrees under the stated hypotheses.
  - The three native-`ℕ` lemmas are proof decomposition declarations; the integer theorem remains the source-facing claim.
- Statement verification status: approved by openai-codex verifier

#### Complete available source proof text

The bounded source slice contains the following complete available reference-solution text; no longer proof is available in scope:

> “0.6 To prove the upper bound, multiply the sum of binomial coeficients by the quantity `( k / n ) ^ { k }` replace this quantity by `( k / n ) ^ { j }` in the left side, and use the binomial theorem. To prove the lower bound, use the definition of the binomial coeficient to express it as a product of k fractions; check that each fraction is lower bounded by `n / k`.”

#### Source proof / prover notes

- `binomial_lower_bound`: write `n.choose k` as the product of the `k` fractions `(n - i) / (k - i)` (or use the factorial identity), and prove each is at least `n / k` from `k ≤ n`; multiply the nonnegative inequalities.
- `choose_le_binomialPartialSum`: the `j = k` summand occurs in `Finset.range (k + 1)` and all other natural binomial summands are nonnegative.
- `binomialPartialSum_upper_bound`: follow the source: multiply by `(k / n)^k`, replace it on the `j`th term by `(k / n)^j` using `j ≤ k` and `k ≤ n`, apply the binomial theorem, then use `(1 + k / n)^n ≤ exp k` to rearrange to `(e * n / k)^k`.
- `binomial_coefficient_bounds`: combine the three native component lemmas without changing the statement.
- `integer_binomial_coefficient_bounds`: first derive positivity of `n` and `k`; rewrite `Int.toNat` and its real casts under those hypotheses; apply the native chain theorem.
