# Formalization Blueprint: HDP/source/pilot_questions.json

- Source: `HDP/source/pilot_questions.json`
- Scoped source slice: `items-1.2`
- Target Lean entry file: `FateXWork/PilotQuestions/Items1261D0ADA8/Main.lean`
- Status: statement fidelity reviewed and Lean proof completed; target module verified.

## Planner Checklist

- [x] Identify definitions and notation that must exist before theorem statements.
- [x] Split large source theorems into Lean-sized lemmas. (No split needed for item 1.2.)
- [x] Record source labels/pages/equations for every generated declaration.
- [x] Check local project and Mathlib names before introducing duplicates.
- [x] Compare the drafted Lean statement against the source document in this planner pass.
- [x] Run independent statement/source verification review and apply corrections.
- [x] Attach the complete source proof text when available, or explicitly record why it is unavailable.
- [x] Record a natural-language proof strategy or source proof pointer for each theorem/lemma.
- [x] Resolve all construction stubs before proof handoff.
- [x] Close the reviewed theorem with a kernel-checked Lean proof; no prover handoff remains.

## Import Plan

Direct Lean imports used by generated/project coverage files:
- `FateXWork/PilotQuestions/Items1261D0ADA8/Main.lean`: `Mathlib.Analysis.Convex.Function`, `Mathlib.Data.Finset.Lattice.Fold`, `Mathlib.Data.Real.Basic`
- `FateXWork/PilotQuestions/Items1261D0ADA8.lean`: `FateXWork.PilotQuestions.Items1261D0ADA8.Main`
- `FateXWork.lean`: `FateXWork.PilotQuestions.Items1261D0ADA8`

## Suggested Search Modules

Non-gating modules or namespaces to search while proving. Do not force these into `.lean` imports unless the prover actually needs them.
- `Mathlib.Analysis.Convex.Function`
- `Mathlib.Data.Finset.Lattice.Fold`
- Theorems likely useful to the prover: `ConvexOn.sup`, `Finset.sup'_mem`, `Finset.sup'_apply`
- Corpus hint: `FateXWork.PilotQuestions.Shared.Convexity` is currently a scaffold-only shared module; no verified reusable declaration was found there for item 1.2.

## Generated File Layout

- Source-backed theorem file: `FateXWork/PilotQuestions/Items1261D0ADA8/Main.lean`
- Aggregator entry file: `FateXWork/PilotQuestions/Items1261D0ADA8.lean`
- Project module root: `FateXWork.lean`
- Default Lake target root coverage: `FateXWork.lean` imports the generated parent module, and the parent module imports `Main`, so project verification reaches this draft.
- No split into `Basic.lean`, `Constructions.lean`, or `Theorems.lean` is justified for this one-item batch.

## Corpus Reuse Notes

- Corpus blueprint records item order `1.1, 1.2` and a candidate edge `1.2 → 1.1` for the shared concept `convexity`; the edge is only a retrieval hint, not a Lean dependency.
- Typed dependency graph records no declared dependencies for item `1.2`.
- Local project search found no existing item `1.2` formalization or local theorem for finite pointwise maxima of convex functions.
- Mathlib search found the binary closure theorem `ConvexOn.sup`; no direct `ConvexOn.finset_sup'` theorem was found. The drafted statement is therefore a finite-family theorem to be proved later from repeated binary sup closure or from `Finset.sup'_mem`.

## Source Statement Inventory

### 1.2

- Kind: question
- Source locator: `HDP/source/pilot_questions.json`, item `1.2`; manifest locator `pilot_questions.json:pdf-pages-28`; scoped extracted text cache `.leanflow/workflow-state/formalization/HDP-source-pilot_questions/batches/items-1.2/extracted.txt`.
- Source statement: `1.2 K Check that the pointwise maximum of a finite number of convex functions is a convex function.`
- Lean namespace: `FateXWork.PilotQuestions.Items1261D0ADA8`
- Planned Lean declarations:
  - `pointwiseFinsetMax`
  - `item_1_2_convexOn_pointwiseFinsetMax`
- Lean statement:
  ```lean
  theorem item_1_2_convexOn_pointwiseFinsetMax {ι E : Type*} [AddCommGroup E]
      [Module ℝ E] (I : Finset ι) (hI : I.Nonempty) (s : Set E)
      (f : ι → E → ℝ) (hf : ∀ i ∈ I, ConvexOn ℝ s (f i)) :
      ConvexOn ℝ s (pointwiseFinsetMax I hI f) := by
    induction hI using Finset.Nonempty.cons_induction with
    | singleton i =>
        simpa [pointwiseFinsetMax] using hf i (by simp)
    | cons i t hi ht ih =>
        rw [show pointwiseFinsetMax (t.cons i hi) (Finset.cons_nonempty hi) f =
            f i ⊔ pointwiseFinsetMax t ht f by
          funext x
          simp [pointwiseFinsetMax, Finset.sup'_cons ht]]
        exact (hf i (by simp)).sup (ih fun j hj => hf j (by simp [hj]))
  ```
- Dependencies: direct imports `Mathlib.Analysis.Convex.Function`, `Mathlib.Data.Finset.Lattice.Fold`, and `Mathlib.Data.Real.Basic`; Mathlib notions `ConvexOn`, `Finset`, `Finset.Nonempty`, `Finset.sup'`, and the real sup-semilattice instance; likely proof dependencies `ConvexOn.sup`, `Finset.sup'_mem`, and `Finset.sup'_apply`.
- Formal statement review: The source asserts closure of convex functions under pointwise maximum for a finite collection. The Lean theorem represents a finite nonempty collection by a finset `I : Finset ι` with proof `hI : I.Nonempty`, a family of real-valued functions `f : ι → E → ℝ`, and a common domain `s : Set E` in a real vector space `E`. The hypothesis `∀ i ∈ I, ConvexOn ℝ s (f i)` states that every function in the finite collection is convex on the same domain. The conclusion states that `pointwiseFinsetMax I hI f`, the pointwise finset maximum implemented as `fun x => I.sup' hI (fun i => f i x)`, is convex on that domain. Specializing `s` to `Set.univ` gives the usual global-function reading.
- Source qualifiers:
  - Mathematical object class: finite nonempty family of real-valued convex functions on a common real vector-space domain.
  - Quantifier order: for every index type `ι`, real vector space `E`, finite nonempty index set `I`, common domain `s`, and family `f`, if each indexed function in `I` is convex on `s`, then the maximum function is convex on `s`.
  - Parameter domain: common subset `s : Set E` of a real vector space; the source leaves the common domain implicit.
  - Output codomain/equality/image condition: real-valued pointwise maximum function `x ↦ max_{i ∈ I} f i x`, represented by `Finset.sup'` over `ℝ`.
  - Side conditions: finite family must be nonempty, since the ordinary maximum of an empty finite family is not specified by the source statement.
  - Follow-on claims: none.
- Lean coverage:
  - `pointwiseFinsetMax I hI f` records the source phrase “pointwise maximum” for a nonempty finite collection as `fun x => I.sup' hI (fun i => f i x)`.
  - `item_1_2_convexOn_pointwiseFinsetMax` states the finite-family closure result for every finite nonempty index set and every common domain `s`.
  - The theorem's `ConvexOn ℝ s` hypotheses and conclusion cover the source word “convex” using Mathlib's standard real-valued convex-function predicate.
- Scope changes: the source suppresses the common domain and the nonemptiness condition; Lean makes both explicit. The domain is generalized from the common whole-space reading to an arbitrary common subset `s`; taking `s = Set.univ` recovers global convex functions. The nonempty finite-family assumption records the ordinary mathematical convention needed for a maximum to be a real-valued function without adding an artificial bottom element.
- Statement verification status: approved. The explicit nonempty finite index set is the faithful Lean representation of an ordinary finite maximum; `s = Set.univ` recovers the source's implicit global-domain reading.
- Complete source proof: unavailable. The scoped source slice and preflight theorem block contain no proof text.
- Source proof / prover notes: The checked proof uses `Finset.Nonempty.cons_induction`. The singleton case simplifies to the sole hypothesis; the cons case rewrites the finite pointwise maximum using `Finset.sup'_cons` and applies `ConvexOn.sup` to the head and induction hypothesis.
- Verification: `lake env lean FateXWork/PilotQuestions/Items1261D0ADA8/Main.lean` and `lake build FateXWork.PilotQuestions.Items1261D0ADA8.Main` both pass with zero errors and zero `sorry`.
- Source proof excerpt: none detected by preflight.
