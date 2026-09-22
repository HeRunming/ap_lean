# AP Lean: book-scale autonomous formalization

AP Lean is a research workspace for building an end-to-end system that turns a
mathematical book into a verified Lean library.

The target is not just to solve isolated benchmark problems. The long-term goal
is to start from a source book, recover its mathematical structure, build a
dependency DAG, and formalize the book automatically from the bottom up while
using Lean verification as the authoritative progress signal.

```text
PDF / TeX source
    ↓
source extraction + visual / textual correction
    ↓
structured mathematical items
(definitions, theorems, lemmas, exercises, foundations)
    ↓
normalization + concept / dependency analysis
    ↓
book manifest + dependency DAG
    ↓
topological scheduling + shared-library planning
    ↓
Lean statement generation
    ↓
theory building + library retrieval + proof search
    ↓
Lean-verified declarations
    ↓
feedback into the DAG, shared library, and campaign state
    ↓
book-scale verified Lean library
```

## Research goal

The central hypothesis of this project is that book-scale autoformalization is
primarily a **dependency-aware theory-building problem**, not only a tactic-level
proof-search problem.

A useful autonomous system should therefore:

1. recover mathematical items from the source with stable source provenance;
2. infer and maintain a dependency DAG rather than treating items independently;
3. schedule formalization bottom-up, only relying on dependencies that are
   already available and verified;
4. create explicit bridge lemmas when Mathlib does not expose the exact
   interface needed by the source mathematics;
5. promote reusable definitions and lemmas into shared modules only after their
   role is justified by verified downstream consumers;
6. treat successful Lean declarations, not searches, tokens, or plausible model
   output, as the main progress metric;
7. keep campaign state, failures, costs, and partial results durable so large
   runs can be resumed and analyzed.

The intended unit of progress is a node moving toward a checked Lean
implementation, eventually reaching a `sorry`-free declaration accepted by the
pinned Lean toolchain.

## What already exists

### 1. Book-source extraction

`fate-x-work/HDP/source/full/` contains the current full-book source artifacts,
including the source PDF, crop metadata, extracted foundations, and a QA corpus.
The extracted question corpus records labels, mathematical text, available
solutions, and source references.

The current HDP campaign source contains 366 question items. A smaller pilot
corpus is kept in `fate-x-work/HDP/source/pilot_questions.json` for fast
end-to-end experiments.

### 2. Dependency and book planning

The pilot already produces structured planning artifacts under
`fate-x-work/FateXWork/PilotQuestions/`:

- `dependency-graph.json` records mathematical nodes and candidate dependency
  edges;
- `book-manifest.json` records concepts, dependency policy, execution order,
  shared-library candidates, and library architecture;
- supporting manifests track declaration placement and campaign state.

This is an early version of the intended DAG layer. Candidate edges are not yet
assumed to be ground truth: full-book dependency recovery, edge validation,
cycle handling, and dynamic graph repair remain active research problems.

### 3. Resumable book-scale campaign machinery

`fate-x-work/FateXWork/Questions/campaign.json` tracks the current HDP
formalization campaign across 367 batches / 366 question items, with explicit
status, provenance, cost, and failure classification.

At the 2026-08-26 snapshot:

- 8 batches are recorded as agent end-to-end proof complete;
- 27 batches have agent-completed statements;
- failures are classified into categories including budget limits, incomplete
  statement generation, infrastructure failures, and incomplete proofs;
- the campaign records explicit spend and budget ceilings.

The point of this machinery is not the current completion percentage by itself;
it is to make a whole-book run inspectable, resumable, and measurable.

### 4. A theory-building case study: FATE-X Problem 3

`fate-x-work/FATEX/3.lean` is completely formalized without `sorry` or `admit`.
The successful proof is intentionally split into intermediate theory rather than
one monolithic proof attempt:

- `FATEX/3_Component.lean` develops the double-coset component / fiber
  mathematics, subgroup orbits, orbit--stabilizer reductions, and equality of
  the relevant relative indices;
- `FATEX/3_Blueprint.lean` turns the compatible left/right-coset equivalence
  into Hall's condition and reconstructs a common transversal;
- `FATEX/3.lean` exposes the original benchmark theorem;
- `experiments/problem3_diagnosis.md` records the failed search-heavy route and
  the theory-building diagnosis that led to the successful decomposition.

Build the checked result with:

```bash
cd fate-x-work
lake update
lake build FATEX.«3_Component» FATEX.«3_Blueprint» FATEX.«3»
```

This case study motivates a core design rule for the larger system: when direct
proof search stalls, the planner should materialize small, explicit Lean bridge
nodes and validate each one before continuing upward.

### 5. LeanFlow as the automation engine

`leanflow/` is the working LeanFlow source snapshot used for the automation
experiments. LeanFlow provides Lean-first workflows for statement generation,
proof repair, project verification, resumable state, research, and bounded
multi-agent execution.

This workspace also contains search-layer changes motivated by the FATE-X
experiments. In particular, explicit natural-language search can use a bounded
direct fallback to `https://leansearch.net` without depending on the Lean LSP
MCP process. The direct fallback returns at most eight results and truncates
large result payloads to limit context blow-up.

See `leanflow/README.md` for the engine-level commands and architecture.

## Target execution model

The desired whole-book loop is approximately:

1. **Extract** source items and preserve exact source locations.
2. **Normalize** notation, item type, concepts, and local context.
3. **Build / repair the DAG** using explicit source references, concept
   structure, local order, retrieved library information, and later Lean
   evidence.
4. **Select ready nodes** whose prerequisites are already verified or available
   in Mathlib / the project library.
5. **Formalize statements** and check that the Lean statement faithfully
   represents the source item.
6. **Prove bottom-up**, introducing verified helper declarations when the proof
   needs missing library bridges.
7. **Promote reusable results** into shared modules when they acquire multiple
   verified consumers or correspond to explicit source-level foundations.
8. **Update campaign state and the DAG** from the verified result, then continue
   upward until the desired dependency closure is complete.

The scheduler should be allowed to revise the graph as formalization teaches us
that an inferred dependency was wrong, incomplete, or better represented by a
new intermediate theorem.

## Current development priorities

1. **Reliable full-book parsing** — improve extraction quality, source locators,
   visual correction, and item typing for definitions, theorems, exercises, and
   proofs.
2. **Full-book DAG construction** — distinguish explicit source dependencies
   from inferred candidate edges; validate dependencies; detect cycles and
   missing foundations; produce a stable topological execution plan.
3. **Dependency-aware autonomous scheduling** — choose formalization work from
   the DAG rather than from a flat item queue, and unblock parents as verified
   children become available.
4. **Theory-building planning** — turn difficult goals into explicit bridge-node
   declarations with mandatory Lean validation at every node.
5. **Shared-library growth** — reuse verified definitions and lemmas across book
   items without allowing speculative scaffolds to become hidden dependencies.
6. **Evaluation** — measure source faithfulness, statement completion, proof
   completion, dependency closure, proof integrity, cost per verified node, and
   failure class.
7. **Book-scale closure** — run the pipeline on increasingly large connected
   subgraphs until an entire book can be formalized from the leaves upward.

## Repository layout

```text
.
├── fate-x-work/
│   ├── HDP/source/                    # source PDF and extracted book artifacts
│   ├── FateXWork/PilotQuestions/      # pilot manifests, DAG, shared-library plan
│   ├── FateXWork/Questions/           # full HDP campaign outputs and state
│   ├── FATEX/                         # FATE-X theory-building case studies
│   └── experiments/                   # diagnostics and experiment notes
└── leanflow/                          # Lean automation engine snapshot
```

The pinned mathematical workspace currently uses Lean 4.28.0.

## Related projects and ideas

This direction is aligned with several useful ideas in the broader Lean
ecosystem:

- [Lean Blueprint](https://github.com/PatrickMassot/leanblueprint) and the
  [Lean project template](https://github.com/leanprover-community/LeanProject)
  use mathematical blueprints, dependency graphs, and formalization status to
  organize large Lean developments.
- [ATLAS](https://github.com/facebookresearch/atlas-lean) explores
  machine-generated textbook formalization at scale and exposes logical
  dependency graphs between source results and Lean declarations.
- [LeanFlow](https://github.com/epfl-lara/LeanFlow) provides the Lean-first
  autonomous workflow engine used as the base of the experiments here.
- [LeanSearch](https://github.com/frenzymath/LeanSearch) provides semantic
  retrieval over Lean / Mathlib declarations.

AP Lean's focus is the end-to-end orchestration problem: turning raw book source
into a dependency-aware, resumable sequence of verified formalization work.

## Snapshot hygiene

Virtual environments, `.lake` build products, caches, LeanFlow runtime state,
and logs are intentionally excluded. No API keys or local credential files are
included.
