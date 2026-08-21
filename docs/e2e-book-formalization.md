# End-to-end book formalization harness

The harness treats extraction, statement formalization, and proof construction
as separate resumable stages. A natural-language solution is evidence, not a
proof-fidelity requirement: any allowed Lean proof that establishes the
approved statement is acceptable.

## Durable item state

Each QA item keeps one stable ID and progresses through:

```text
extracted
  -> dependencies_resolved
  -> statement_drafted
  -> statement_elaborates
  -> statement_fidelity_approved
  -> proof_pending
  -> proof_verified
  -> project_verified
```

Failed attempts do not overwrite the last verified state. They append an
attempt record containing model/runtime identity, prompt inputs, retrieved
declarations, patch, diagnostics, axiom profile, elapsed time, and failure
classification.

At book scope, `campaign.json` stores the coarser batch state separately:

```text
pending -> statements_completed -> proofs_completed
       \-> statement_retry       \-> proof_retry
```

A successful `formalize` process therefore exits with code zero and advances
only to `statements_completed`, even though its intentional proof `sorry`s mean
the mathematics is not yet project-verified. The later `prove` process is the
only stage allowed to advance the batch to `proofs_completed`. Attempts remain
append-only, so a resumed book run does not pay to regenerate approved
signatures.

The campaign's dollar total is an execution guard, not an estimate inferred
from lifetime session counters. A full-book run requires an explicit budget;
pilot runs stay bounded while model and auxiliary-reviewer costs are calibrated.
The campaign runner is dry-run by default:

```bash
python -m leanflow_cli.formalization.corpus_campaign_runner \
  BookFormalization/campaign.json --project-root .
```

One real action requires both an explicit `budget_usd` in the campaign and a
per-action reservation, for example `--execute --reserve-usd 5`. The default
executes exactly one stage and exits, so an external harness can inspect the
durable outcome before admitting another paid action.

After single-worker calibration, `--workers N` leases a proof-first wave of
distinct items and runs it concurrently. The scheduler reserves `N` times the
per-action amount before launch, reduces `N` when the remaining budget is
smaller, and records each completion through a process-safe campaign
transaction. Leases expire after two hours by default and can be changed with
`--lease-ttl-seconds`. For example:

```bash
python -m leanflow_cli.formalization.corpus_campaign_runner \
  BookFormalization/campaign.json --project-root . \
  --execute --workers 4 --reserve-usd 3
```

Workers operate on distinct item targets. Shared-library promotion remains a
separate verified transaction; workers propose reusable declarations locally
instead of concurrently editing a shared module.

Production waves can separately cap model workers and Lean-heavy subprocesses,
and route routine work to cheaper models before escalation:

```bash
python -m leanflow_cli.formalization.corpus_campaign_runner \
  BookFormalization/campaign.json --project-root . --execute \
  --workers 8 --lean-slots 3 --reserve-usd 3 \
  --reasoning-effort medium \
  --statement-model gpt-5.6-terra --proof-model gpt-5.6-terra \
  --escalation-model gpt-5.6-sol --escalate-after-failures 2
```

The runner refreshes declared dependencies from `book-manifest.json` before
leasing. A statement enters the frontier after every declared predecessor has
an agent-approved statement; its proof enters only after those predecessor
proofs are kernel-checked. Inferred shared-concept edges remain retrieval hints
and never serialize otherwise independent work. Infrastructure failures do not
count toward strong-model escalation.

## Stage boundaries

1. The parser emits immutable QA JSON with page-aligned provenance.
2. Intake normalizes items and recovers a dependency DAG. Cycles and unresolved
   references are explicit diagnostics rather than guessed ordering.
3. A statement worker drafts definitions and theorem signatures. It may create
   bridge definitions when Mathlib does not model the source vocabulary.
4. Lean checks syntax, imports, types, namespaces, and declaration collisions.
5. An independent fidelity reviewer compares the elaborated signature with the
   source question. It checks quantifiers, domains, hypotheses, conclusions,
   coercions, edge cases, and any intentional scope change.
6. The proof queue works only on approved signatures. Retrieval combines exact
   declaration search, local project search, semantic LeanSearch, and bounded
   source inspection. Retrieved names are checked in Lean before entering the
   proof prompt.
7. Candidate edits pass incremental Lean checking, warning and axiom policy,
   then a file/project build. Only verified candidates advance item state.
8. The output repository and a provenance map are generated from the durable
   records, never reconstructed from model chat history.

## Corpus compiler preflight

For QA JSON, intake first compiles the complete item collection into four
durable book-level artifacts next to the generated Lean workspace:

- `BookBlueprint.md` is the bounded, human-readable concept and dependency summary;
- `book-manifest.json` retains every item, concept, and typed dependency edge;
- `dependency-graph.json` is the machine-readable graph used for scoped retrieval;
- `reuse-registry.json` records duplicate declaration candidates, verified consumers,
  and promotion status without trusting chat history;
- `library-architecture.json` groups recurring concepts into conservative domain modules;
- `declaration-placement.json` classifies generated reusable declarations as
  `local_default`, `placement_review`, `promotion_candidate`, or
  `approved_for_promotion`; a recommendation becomes
  `blocked_local_dependency` when moving it would strand a referenced local declaration;
- `campaign.json` records statement/proof stage completion, append-only attempts,
  and the explicit full-run budget;
- `Shared/Basic.lean` is the empty, verified landing zone for genuinely reusable code.

Source-declared dependencies are recorded as `declared_unverified`. Inferred
`shared_foundation` edges are only candidates: they are same-chapter retrieval
hints based on selective shared concepts and do not authorize imports or theorem
applications. A scoped run receives only its selected items and immediate
incoming edges, not the complete graph.

The execution plan topologically sorts only source-declared dependencies and
uses source order to break ties. Candidate edges never affect scheduling.
Unresolved dependency IDs and cycles make the plan explicitly unschedulable;
the reported order remains deterministic for diagnosis rather than pretending
that the graph is valid.

Move a declaration into `Shared/Basic.lean` only when it has an explicit
source-level role or at least two Lean-verified consumers. The planner may
recommend this promotion, but project verification is the authority.
Regeneration preserves the registry's durable `promotions` records. Exact
duplicate source declarations may populate `duplicate_candidates`, but LeanFlow
never rewrites their namespaces or consumers automatically.

The library architect currently recognizes conservative `Convexity`,
`Probability`, `LinearAlgebra`, and `Analysis` clusters. It emits empty,
Mathlib-backed module scaffolds only when at least one concept recurs across two
items. Scaffolds are not imported automatically; the scoped planner sees only
modules relevant to its selected items.

Placement analysis runs after the target Lean scaffold exists. It inspects only
reusable construction declarations (`def`, `abbrev`, `structure`, `class`, and
`instance`), routes their mathematical concepts to the library architecture,
and records a declaration digest. A recommendation does not move code.
`approved_for_promotion` is possible only when the matching digest has eligible
reuse-registry evidence.

Before emitting a transaction candidate, placement analysis constructs the
same-file dependency closure between reusable declarations. A declaration may
move only when each local dependency is already approved or can move into the
same recommended module. Transaction candidates contain source path, digest,
target module, and dependency list; they still require candidate-file and
project verification before any source mutation.

The first dry-run mode is identity-preserving relocation: it wraps the moved
declaration in its original complete namespace, removes it only from the
candidate source image, and adds the target-module import. This proves that the
physical module boundary can change without changing the declaration's Lean
API identity. Canonicalizing an item-specific namespace into a shared public
namespace is a separate API-migration transaction and must also update every
verified consumer.

## Acceptance order

Accept/revert is lexicographic rather than a vague “improved” score:

1. Never weaken or mutate an approved source-backed signature during proving.
2. Reject candidates with elaboration errors, open goals, forbidden axioms, or
   new unrelated `sorry` declarations.
3. Prefer fewer target-local errors and goals, then fewer target-local sorries.
4. Prefer smaller dependency surfaces and patches after correctness ties.
5. Require the final file and project build before `proof_verified` becomes
   `project_verified`.

## Failure taxonomy

Every terminal attempt must choose one primary class:

- `extraction`: missing/garbled statement or source locator;
- `dependency`: missing definition, notation, or earlier item;
- `statement_translation`: Lean signature does not express the source claim;
- `library_retrieval`: suitable Mathlib support exists but was not found;
- `library_gap`: required bridge theory is absent;
- `proof_search`: statement and support are adequate but construction failed;
- `lean_api`: proof idea is sound but declaration/tactic names were mislocated;
- `infrastructure`: verifier, toolchain, transport, or resource failure.

This classification is the main optimization signal. Aggregate pass rate alone
cannot distinguish a better prover from a better parser or declaration index.

## Repository layout

Split by dependency and semantic unit before files become long:

```text
BookFormalization/
  BookBlueprint.md    # bounded corpus plan
  book-manifest.json  # full concept/item index
  dependency-graph.json
  reuse-registry.json
  library-architecture.json
  declaration-placement.json
  Shared/
    Basic.lean        # verified shared vocabulary and bridge definitions
    Convexity.lean    # created only when the corpus justifies the domain
  Chapter01/
    Statements.lean   # reviewed signatures
    Proofs.lean       # verified implementations or theorem bodies
  BookFormalization.lean
.leanflow/
  formalization/items.json
  formalization/attempts.jsonl
  formalization/provenance.json
```

The exact module count is adaptive, but stable QA IDs and declaration names do
not change merely because files are reorganized.
