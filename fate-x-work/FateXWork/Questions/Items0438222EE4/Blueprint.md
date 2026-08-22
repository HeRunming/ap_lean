# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Scoped source slice: `items-0.4`, extracted at `.leanflow/workflow-state/formalization/HDP-source-full-qa-questions/batches/items-0.4/extracted.txt`
- Target Lean entry file: `FateXWork/Questions/Items0438222EE4/Main.lean`
- Status: source map and declaration plan are drafted; awaiting independent statement/source verification after the Lean skeleton is checked.

## Planner Checklist

- [x] Identify definitions and notation that must exist before theorem statements.
- [x] Split the source item into Lean theorem-sized part (a) and part (b) declarations.
- [x] Record the source locator and available source proof text.
- [x] Check local project and Mathlib names before introducing declarations.
- [x] Compare the planned Lean statements with the bounded source slice.
- [ ] Run independent statement/source verification review and apply any corrections.
- [x] Record the complete available source proof text and its limitation.
- [x] Record source-aware proof strategies for both declarations.
- [x] Confirm that the plan has no definition, structure, class, or instance construction stubs.
- [ ] Mark theorem `sorry` declarations ready for a user-started prove workflow. (Reserved for the independent review.)

## Import Plan

Direct Lean imports in `Main.lean`:

- `FateXWork.Questions.Shared.Analysis`

## Suggested Search Modules

Non-gating modules or namespaces to search during a later proof workflow. They are not direct imports in the draft.

- `FateXWork.Questions.Shared.Probability` for the independent-random-sign proof of part (a)
- `Mathlib.Analysis.InnerProductSpace.PiL2` for `EuclideanSpace.norm_sq_eq` and the coordinate basis
- `Mathlib.Data.Real.Sqrt` for square-root order facts
- `Mathlib.Algebra.BigOperators` for finite-sum identities

## Generated File Layout

- Single source-aligned entry module: `FateXWork/Questions/Items0438222EE4/Main.lean`
- Aggregator module: `FateXWork/Questions/Items0438222EE4.lean` imports `FateXWork.Questions.Items0438222EE4.Main`.
- Root module coverage: `FateXWork.lean` directly imports `FateXWork.Questions.Items0438222EE4`.
- No split is planned: the source contains one two-part question and its two declarations share the same finite-dimensional representation.

## Definitions and Representation

- `FateXWork.Questions.Shared.RealN n` is the existing shared abbreviation for the book's `ℝⁿ`, namely Mathlib's `EuclideanSpace ℝ (Fin n)`.
- A sign assignment is represented explicitly as `ε : Fin n → ℝ` with pointwise predicate `ε i = 1 ∨ ε i = -1`.
- The displayed signed sum is `∑ i, ε i • x i`. This retains one independently chosen sign for each input vector, rather than encoding signs indirectly.

## Source Statement Inventory

### 0.4

- Title: Balancing vectors
- Kind: question, parts (a) and (b).
- Source locator: `HDP/source/full/qa/questions.json`, scoped entry `[0.4]`, lines 1–7 of `.leanflow/workflow-state/formalization/HDP-source-full-qa-questions/batches/items-0.4/extracted.txt`.
- Planned Lean declarations:
  - `balancing_vectors_exists` for part (a):
    ```lean
    theorem balancing_vectors_exists (n : ℕ) (x : Fin n → RealN n)
        (hx : ∀ i, ‖x i‖ ≤ 1) :
        ∃ ε : Fin n → ℝ,
          (∀ i, ε i = 1 ∨ ε i = -1) ∧
            ‖∑ i, ε i • x i‖ ≤ Real.sqrt (n : ℝ)
    ```
  - `balancing_vectors_sharp` for part (b):
    ```lean
    theorem balancing_vectors_sharp (n : ℕ) :
        ∃ x : Fin n → RealN n,
          (∀ i, ‖x i‖ ≤ 1) ∧
            ∀ ε : Fin n → ℝ,
              (∀ i, ε i = 1 ∨ ε i = -1) →
                ‖∑ i, ε i • x i‖ = Real.sqrt (n : ℝ)
    ```
- Dependencies:
  - shared representation `FateXWork.Questions.Shared.RealN`;
  - finite sums over `Fin n`, real scalar multiplication, norms, and `Real.sqrt` from Mathlib;
  - the later proof of part (a) is expected to use independent random signs and the expectation of the squared norm, as indicated by the source.

#### Formal statement review

The source fixes vectors `x₁, …, xₙ` in the unit Euclidean ball of `ℝⁿ`. In Lean this is `x : Fin n → RealN n` and `∀ i, ‖x i‖ ≤ 1`. The source conclusion in part (a) is existential over one sign per vector; Lean uses an explicit `ε : Fin n → ℝ`, requires every value to be `1` or `-1`, and bounds the norm of the corresponding finite sum by `Real.sqrt (n : ℝ)`. Thus the object class, finite indexing, unit-ball hypothesis, sign condition, and output-radius inequality are all theorem clauses.

For part (b), the informal phrase “cannot be reduced in general” is made checkable by asserting, in each dimension, the existence of a unit-ball configuration for which every sign assignment has signed-sum norm exactly `√n`. This supplies the standard orthonormal-coordinate witness internally through an existential statement and directly rules out every strictly smaller uniform radius.

- Source qualifiers:
  - ambient vectors are in `ℝⁿ`;
  - there are `n` individually indexed vectors;
  - every input vector has Euclidean norm at most one;
  - a choice of `±` sign is made for each vector;
  - the conclusion of (a) bounds the Euclidean norm of the signed sum by `√n`;
  - (b) claims global sharpness, not merely an example of a large sum for one fixed sign choice.
- Lean coverage: exact for the source's ambient space, indexing, unit-ball hypotheses, signs, radius bound, and sharpness claim.
  - `RealN n` is the shared, explicit bridge from the source's `ℝⁿ` to `EuclideanSpace ℝ (Fin n)`;
  - `Fin n → RealN n` and `∀ i` encode all `n` input vectors and their individual unit-ball hypotheses;
  - `ε : Fin n → ℝ` with the pointwise disjunction encodes the signs;
  - `‖∑ i, ε i • x i‖ ≤ Real.sqrt (n : ℝ)` is the exact radius assertion for (a);
  - the universal-in-`ε` equality in `balancing_vectors_sharp` gives the sharpness witness required for (b).
- Scope changes: none. Lean also admits the harmless `n = 0` boundary case as a totalization of the source's conventional positive-dimensional reading; no source assumption is omitted. The coordinate basis witness in part (b) is not named in the theorem statement, but its existence and every-sign equality are retained, so there is no representation omission.
- Statement verification status: approved by openai-codex verifier

#### Complete available source proof text

No full reference proof is present in the bounded source slice. The entire available reference-solution text is:

> “0.4 (a) Select the signs independently at random. Calculate the expected squared norm of the random vector `±x₁ ±x₂ ± ··· ±xₙ` using Example 0.3.”

The source supplies no written proof text for part (b); the standard sharpness explanation is the coordinate-vector configuration.

#### Source proof / prover notes

- `balancing_vectors_exists`: assign independent Rademacher signs. Expanding the expected squared norm cancels all mixed terms, leaving `∑ i ‖x i‖² ≤ n`; some outcome has squared norm at most that expectation, hence norm at most `√n`. The later prover should search the suggested probability module before constructing any probability space manually.
- `balancing_vectors_sharp`: use the `n` coordinate unit vectors in `RealN n`. Every signed sum has coordinates all equal to `±1`, so its squared norm is `n` and its norm is `√n`. This witnesses that no uniformly smaller radius works.
