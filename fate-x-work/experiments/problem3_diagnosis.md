# FATE-X Problem 3 theory-building pilot

Date: 2026-08-16

## Result

The original theorem was first reduced to one mathematical obligation in
`FATEX/3_Blueprint.lean`: `cosetIncident_hall`. That obligation has now been
discharged, and both `FATEX/3_Component.lean` and `FATEX/3_Blueprint.lean`
build without `sorry` or `admit`.

The reduction is:

1. Define incidence between a left coset and a right coset by nonempty
   intersection.
2. Use Hall's theorem to turn the neighborhood-cardinality inequality into an
   injective compatible map.
3. Use equality of the finite left/right quotient cardinalities to upgrade the
   map to an equivalence.
4. Choose one common representative for each matched pair and prove directly,
   through Mathlib's `IsComplement` quotient characterizations, that its range
   is both a left and right transversal.

The former remaining statement was:

```lean
lemma cosetIncident_hall
    (G : Type) [Group G] (H : Subgroup G) [H.FiniteIndex] :
    ∀ A : Finset (LeftCosets G H),
      A.card ≤ Set.ncard
        {r : RightCosets G H | ∃ q ∈ A, CosetIncident G H q r}
```

## Strong-model run

- Provider/model: Codex, GPT-5.5, xhigh
- API calls: 13
- Input tokens: 574,534
- Output tokens: 10,558
- Estimated cost: USD 6.0621
- Verified proof candidates submitted: 0
- Invalid speculative candidates: 1 (a guessed nonexistent Mathlib theorem)
- Library/search calls before guard intervention: 12
- Decomposer wait before manual interruption: 396 seconds

The model identified the intended double-coset/component proof, but repeatedly
searched broad Mathlib files and did not materialize an intermediate Lean
lemma. After the search guard fired it invoked `lean_decompose_helpers`; the
advisor returned no helper in 396 seconds and was interrupted. Optional
`lean-proof-auto` and `lean-explore` MCP servers were unavailable, while the
core LeanInteract verifier was healthy.

## Diagnosis

- Primary class: `library_interface`
- Confidence: medium-high after the checked reduction
- Operational amplifier: search-output/context blow-up and a hanging
  decomposition advisor
- Not supported by evidence: statement mismatch, false mathematical strategy,
  or ordinary tactic-level proof-search failure

The next useful node is not another direct attempt at `cosetIncident_hall`.
It is a formally stated component-counting lemma for a fixed double coset,
followed by a finite disjoint-union/cardinality lemma that derives Hall's
condition.

## Harness changes suggested by the run

1. Cap search result payloads, not only the number of search calls.
2. After a bounded number of searches, require a concrete Lean declaration,
   rather than permitting a guessed top-level theorem name.
3. Give decomposer calls a shorter heartbeat deadline and preserve partial
   structured output.
4. Count `verified candidate submissions` as the progress metric; token use and
   search diversity are not progress.
5. Seed the planner with the checked reduction already present in the source,
   so it cannot restart from the original common-transversal theorem.

## LeanSearch follow-up

LeanFlow now has a bounded direct `leansearch.net` fallback for explicit
`natural-language` searches. It does not depend on the Lean LSP MCP process,
returns at most eight declarations, and truncates each formal statement and
informal description to 400 characters. The default `auto` route deliberately
does not use this public HTTP fallback, so ordinary proof search keeps its
low-latency behavior.

An end-to-end query for "Hall marriage theorem for a finite relation" returned
`Fintype.all_card_le_filter_rel_iff_exists_injective` as the first result, with
the expected statement from `Mathlib.Combinatorics.Hall.Basic`. Queries for the
fixed-double-coset counting step returned nearby union/coset declarations but
no theorem proving the required component cardinality. This sharpens the
diagnosis: semantic retrieval successfully finds the existing Hall interface;
the remaining blocker is synthesis and formalization of the missing bridge
lemma, not failure to retrieve a known Mathlib declaration.

## First verified bridge node

`FATEX/3_Blueprint.lean` now contains checked maps from left and right cosets
to `DoubleCoset.Quotient H H`, together with the fully proved equivalence

```lean
CosetIncident G H q r ↔ leftComponent G H q = rightComponent G H r
```

The entire blueprint still compiles, with `cosetIncident_hall` as its only
`sorry`. Consequently the remaining mathematical interface is narrower than
the original component-counting description: prove that corresponding fibers
of `leftComponent` and `rightComponent` have equal finite cardinality, then
apply a generic fiber-union cardinality argument.

The numerical core of the fiber balance is now also checked. For any
finite-index subgroups `H` and `K` with equal index, the blueprint proves

```lean
(H ⊓ K).relIndex H = (H ⊓ K).relIndex K
```

and specializes it to `K = H.map (MulAut.conj g).toMonoidHom`. The proof uses
`Subgroup.relIndex_mul_index`, cancellation by the common nonzero global
index, and `Subgroup.index_map_equiv`. This is important for infinite groups:
it avoids the invalid argument of cancelling the cardinality of `H` itself.
That interface work is now complete. Each component fiber is identified with
an orbit of `H`; orbit--stabilizer identifies its cardinality with a subgroup
index; the left and right stabilizer indices are rewritten as the two relative
indices above. `Equiv.sigmaFiberEquiv` then assembles the fiberwise bijections
into a global compatible equivalence between left and right cosets.

Finally, the compatible equivalence gives the Hall inequality by
`Set.ncard_le_ncard_of_injOn`, and the pre-existing reconstruction lemma turns
it into a set that is simultaneously a left and right transversal. The formal
build command

```text
lake build FATEX.«3_Component» FATEX.«3_Blueprint»
```

completed successfully on Lean 4.28.0 / the pinned Mathlib environment.

The benchmark source `FATEX/3.lean` now imports the checked blueprint and uses
the resulting theorem directly. `lake build FATEX.«3»` completed successfully,
so Problem 3 itself, not only the auxiliary development, is now solved without
placeholders.
