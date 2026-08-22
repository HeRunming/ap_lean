# Formalization Blueprint: HDP/source/full/foundations/theorem-0.0.2.json

- Source: `HDP/source/full/foundations/theorem-0.0.2.json`
- Target Lean entry file: `FateXWork/Theorem002/Main.lean`
- Status: planner preflight created; replace this with the agent's dependency plan.

## Planner Checklist

- [ ] Identify definitions and notation that must exist before theorem statements.
- [ ] Split large source theorems into Lean-sized lemmas.
- [ ] Record source labels/pages/equations for every generated declaration.
- [ ] Check local project and Mathlib names before introducing duplicates.
- [ ] Verify drafted Lean statements match the source document.
- [ ] Run independent statement/source verification review and apply corrections.
- [ ] Attach the complete source proof text when available, or explicitly record why it is unavailable.
- [ ] Record a natural-language proof strategy or source proof pointer for each theorem/lemma.
- [ ] Resolve all construction stubs before proof handoff.
- [ ] Mark stable theorem/lemma/example `sorry` declarations ready for a user-started prove workflow. (Only check after independent review approves every source entry.)

Replace all `_pending_` entries before drafting Lean. The managed workflow treats this initial
blueprint as a placeholder, not as a completed plan.

For each theorem or lemma, include proof guidance useful to the prover: the complete source proof
when available, relevant source proof paragraphs, induction variables, reductions, important
previously planned lemmas, and any known statement-fidelity caveats. Lean doc comments should include compact proof notes;
the generated supplemental blueprint skill carries the durable `Blueprint.md` reference.

## Import Plan

Direct Lean imports expected in generated Lean files only:
- `Mathlib`

## Suggested Search Modules

Non-gating modules or namespaces to search while proving. Do not force these into `.lean` imports unless the prover actually needs them.
- [none yet]

## Generated File Layout

- Aggregator entry file: `FateXWork/Theorem002/Main.lean`
- Split into `Basic.lean`, `Constructions.lean`, and `Theorems.lean` when declaration count, proof count, or source sections justify it.

## Source Statement Inventory

### foundation:0.0.2

- Kind: theorem
- Source locator: `HDP-2.pdf:physical-page-10:Theorem-0.0.2`
- Planned Lean declarations: _pending_
- Dependencies: 0.3
- Formal statement review: _pending_
- Source qualifiers: _pending_
- Lean coverage: _pending_
- Scope changes: _pending_
- Statement verification status: _pending_
- Complete source proof: _pending_
- Source proof / prover notes: _pending_
- Source proof excerpt: The book proves this by representing x as the expectation of a random vector Z supported on T, taking k independent copies Z_1, ..., Z_k, and using the variance-of-a-sum identity to show that the expected squared approximation error is at most 1/k; therefore at least one realization attains the bound.

Consider a set T contained in the unit Euclidean ball in R^n. For every x in conv(T) and every positive natural number k, there exist points x_1, ..., x_k in T, with repetitions allowed, such that the Euclidean norm of x - (1/k) * sum_{j=1}^k x_j is at most 1/sqrt(k). The selected points have equal weights.
