# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Scoped source slice: `items-0.3`, extracted cache lines 1–4 (the complete bounded source text).
- Target Lean entry file: `FateXWork/Questions/Items039886646C/Main.lean`
- Status: Lean declarations drafted and file-verified; awaiting independent statement/source verification

## Planner Checklist

- [x] Identify definitions and notation that must exist before theorem statements.
- [x] Check local project and Mathlib names before introducing duplicates.
- [x] Record source labels/locators, dependencies, statement-fidelity review, and prover notes.
- [x] Draft the source-backed Lean declarations with theorem proofs left as `sorry`.
- [x] Run independent statement/source verification review and apply corrections.
- [ ] Mark stable theorem/lemma/example `sorry` declarations ready for a user-started prove workflow. (Only after independent review approves every source entry.)

## Import Plan

Direct Lean imports in `Main.lean`:

- `FateXWork.Questions.Shared.Probability`

## Suggested Search Modules

Non-gating proof-search hints; do not add these imports unless a prover needs them.

- `Mathlib.Probability.Moments.Variance`
- finite-sum inner-product and Bochner-integral linearity lemmas in `Mathlib.Analysis.InnerProductSpace` and `Mathlib.MeasureTheory.Integral.Bochner`

## Generated File Layout

- `FateXWork/Questions/Items039886646C/Main.lean`: the implemented `HasMeanZero` predicate and the single source theorem skeleton.
- `FateXWork/Questions/Items039886646C.lean` already aggregates `Main`; `FateXWork.lean` already imports that aggregator, so a project build covers this declaration.
- No split is justified for one source question at this drafting stage.

## Definitions and Dependency Plan

- `HasMeanZero`: an implemented predicate saying that the Bochner expectation of a `RealN n`-valued random vector is zero.  It records the source phrase “mean-zero” without creating a construction stub.
- `FateXWork.Questions.Shared.RealN n`: the project’s abbreviation for `EuclideanSpace ℝ (Fin n)`, representing the source space `ℝ^n`.
- `FateXWork.Questions.Shared.integrable_inner_of_indepFun`: establishes integrability of an off-diagonal inner product from integrability and `IndepFun`.
- `FateXWork.Questions.Shared.integral_inner_eq_inner_integral_of_indepFun`: factors an off-diagonal inner-product expectation into the inner product of the two expectations.
- The eventual proof will additionally use `‖v‖ ^ 2 = inner ℝ v v`, finite double-sum expansion of the inner product of a finite sum, and linearity of the Bochner integral.  Off-diagonal terms vanish by the two shared results and `HasMeanZero`; diagonal terms are exactly the summands on the right.

## Source Statement Inventory

### 0.3

- Title: Variance of a sum
- Kind: question
- Source locator: `HDP/source/full/qa/questions.json`, item `0.3`, scoped extracted source lines 1–5 (the source has no separate page or theorem label).
- Planned Lean declarations:
  - `FateXWork.Questions.Items039886646C.HasMeanZero`
  - `FateXWork.Questions.Items039886646C.integral_norm_sq_sum_eq_sum_integral_norm_sq`
- Dependencies: `FateXWork.Questions.Shared.Probability` (and its `RealN` interface); finite-indexed sums; Bochner integrals; pairwise `ProbabilityTheory.IndepFun`.
- Formal statement review: `integral_norm_sq_sum_eq_sum_integral_norm_sq` expresses the source equality using the Bochner expectation of a finite `RealN n`-valued family. `HasMeanZero` is definitionally the vector integral being zero. The conclusion and Euclidean-space representation match the displayed source identity; the explicit analytic and independence representation changes are recorded below.
- Source qualifiers: finite family `Z₁,…,Zₖ` in `ℝⁿ`; independence; zero vector expectation for each `Z_j`; equality of expectations of squared Euclidean norms; the prompt leaves probability-space and moment hypotheses implicit.
- Lean coverage: `Z : Fin k → Ω → RealN n`; `[IsProbabilityMeasure μ]`; `h_mean_zero`; pairwise `IndepFun` in `h_indep`; finite first and second moments in `h_integrable` and `h_sq_integrable`; and the equality of the corresponding integrals of norm squares.
- Scope changes: pairwise `IndepFun` represents the pairwise consequence needed from source independence, without a bridge from a chosen mutual-independence predicate; finite first/second moments and a probability Bochner-integral model are explicit where the source is implicit; no extended-real infinite-moment claim is formalized.
- Complete source proof: unavailable — the bounded source slice gives only the question and its scalar-variance motivation.
- Source proof / prover notes: rewrite the squared norm as a self-inner-product, expand to a finite double sum, retain diagonal terms, and use the shared independence factorization plus zero means to kill off-diagonal terms.
- Statement verification status: approved by openai-codex verifier

```lean
namespace FateXWork.Questions.Items039886646C

def HasMeanZero {Ω : Type*} [MeasurableSpace Ω] {μ : MeasureTheory.Measure Ω}
    {n : ℕ} (X : Ω → FateXWork.Questions.Shared.RealN n) : Prop :=
  (∫ ω, X ω ∂μ) = 0

theorem integral_norm_sq_sum_eq_sum_integral_norm_sq
    {Ω : Type*} [MeasurableSpace Ω] {μ : MeasureTheory.Measure Ω}
    [MeasureTheory.IsProbabilityMeasure μ]
    {k n : ℕ} (Z : Fin k → Ω → FateXWork.Questions.Shared.RealN n)
    (h_integrable : ∀ j, MeasureTheory.Integrable (Z j) μ)
    (h_sq_integrable : ∀ j, MeasureTheory.Integrable (fun ω => ‖Z j ω‖ ^ 2) μ)
    (h_indep : Pairwise (fun i j => ProbabilityTheory.IndepFun (Z i) (Z j) μ))
    (h_mean_zero : ∀ j, HasMeanZero (μ := μ) (Z j)) :
    (∫ ω, ‖∑ j, Z j ω‖ ^ 2 ∂μ) = ∑ j, ∫ ω, ‖Z j ω‖ ^ 2 ∂μ := by
  sorry

end FateXWork.Questions.Items039886646C
```

#### Source statement

For independent mean-zero random vectors `Z₁, …, Zₖ ∈ ℝⁿ`, the source asks to verify

```text
E ‖∑_{j=1}^k Z_j‖₂² = ∑_{j=1}^k E ‖Z_j‖₂².
```

#### Formal statement review

The drafted theorem quantifies over a measurable sample space `Ω`, a probability measure `μ`, natural numbers `k` and `n`, and a family `Z : Fin k → Ω → RealN n`.  Its conclusion is the displayed identity with `E` represented by the Bochner integral `∫ ω, · ∂μ` and the Euclidean two-norm represented by Lean’s norm on `RealN n`.  `HasMeanZero` unfolds exactly to the vector-valued expectation being zero.  The draft is source-faithful in conclusion and in the Euclidean-vector representation, subject to the explicit analytic and independence representation changes below.

#### Source qualifiers

- A finite family indexed as `Z₁, …, Zₖ` of vectors in `ℝⁿ`.
- Independence of the random vectors.
- Mean-zero vector expectation for every member of the family.
- Equality of real-valued expectations of squared Euclidean norms.
- The source does not spell out measure-space, probability-measure, measurability, or finite-second-moment hypotheses.

#### Lean coverage

- `Z : Fin k → Ω → RealN n` covers the finite family and `ℝⁿ` representation; `RealN n = EuclideanSpace ℝ (Fin n)` is the explicit project bridge.
- `[IsProbabilityMeasure μ]` makes the Bochner integral an expectation on a probability space.
- `h_mean_zero : ∀ j, HasMeanZero (μ := μ) (Z j)` covers mean zero.
- `h_indep : Pairwise (fun i j => IndepFun (Z i) (Z j) μ)` supplies precisely the pairwise independence used by the variance expansion.
- `h_integrable` and `h_sq_integrable` state the first- and second-moment finiteness needed for Bochner-integral and inner-product manipulations.
- The conclusion is `∫ ω, ‖∑ j, Z j ω‖ ^ 2 ∂μ = ∑ j, ∫ ω, ‖Z j ω‖ ^ 2 ∂μ`.

#### Scope changes

- The source’s unqualified word “independent” is represented by pairwise `IndepFun`, which is sufficient for this identity.  This generalizes the needed hypothesis but does not formalize a bridge from a chosen definition of mutual/joint independence to pairwise independence.
- Finite first and second moments are added explicitly.  They are necessary for the chosen finite Bochner-integral formulation; the source prompt leaves this regularity implicit.
- The source’s expectation notation is fixed to a probability measure and Bochner integrals.  No extended-real/infinite-moment interpretation is claimed.

#### Complete source proof

No proof is present in the bounded source slice: it contains the question and its motivating scalar variance identity only.

#### Source proof / prover notes

Rewrite the square of the norm as the self-inner-product and expand the inner product of the finite sum into a finite double sum.  On a diagonal term `i = j`, recover `∫ ‖Z i‖²`.  On an off-diagonal term, use `integral_inner_eq_inner_integral_of_indepFun` with pairwise independence; both vector integrals are zero by `HasMeanZero`, so the term vanishes.  Use `integrable_inner_of_indepFun` and `h_sq_integrable` to justify interchange of finite sums and integrals.

- Statement verification status: approved by openai-codex verifier
