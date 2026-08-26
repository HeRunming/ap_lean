# AP Lean / LeanFlow Harness

**A research harness for autonomous, book-scale mathematical formalization in Lean 4.**

The long-term goal is not only to prove isolated Lean theorems. It is to build an
end-to-end system that can start from a mathematical book, recover its mathematical
structure, and formalize the resulting theory in dependency order until the whole
selected corpus is kernel-verified.

```text
PDF / TeX / structured QA source
        │
        ▼
source extraction + provenance
        │
        ▼
mathematical items + source foundations
        │
        ▼
book manifest + typed dependency graph
        │
        ▼
dependency-aware ready frontier
        │
        ▼
statement formalization
        │
        ▼
theory building + proof search + retrieval
        │
        ▼
Lean verification
        │
        ├──► promote reusable verified declarations
        ├──► refine dependencies / shared library
        └──► checkpoint campaign state
                     │
                     ▼
          repeat until the book closes
```

The working thesis is that **book-scale autoformalization is primarily a
dependency-aware theory-building problem, not only a tactic-level proof-search
problem**. A plausible proof, a successful search, or a large amount of model work is
not progress by itself. The durable unit of progress is a source-grounded declaration
that Lean has verified and that later nodes can safely reuse.

## Research goal

We want a system that can take a source such as a PDF textbook and autonomously:

1. extract definitions, theorems, propositions, examples, exercises, and useful source
   foundations while retaining source provenance;
2. normalize those objects into stable mathematical items;
3. recover explicit and implicit dependencies and construct a book-level DAG;
4. schedule work from the bottom of the DAG upward, prioritizing nodes whose hard
   prerequisites are already verified;
5. translate informal statements into faithful Lean declarations;
6. synthesize missing bridge lemmas when Mathlib does not expose the exact interface
   needed by the source mathematics;
7. prove each node with bounded retrieval, proof search, decomposition, and reusable
   local theory building;
8. verify every promoted declaration with Lean before it becomes a dependency for
   downstream work;
9. promote genuinely reusable verified material into a shared local library; and
10. persist failures, costs, dependencies, checkpoints, and partial progress so a
    long-running whole-book campaign can resume rather than restart.

The dependency graph is therefore both a planning artifact and an evolving research
object. Some edges come directly from the source, some are inferred as useful
foundations, and additional dependencies may only become visible while formalizing a
node. The system should be able to validate, reject, and repair those edges over time.

## What already exists on this branch

This branch contains substantially more of that pipeline than the root README
previously described.

### 1. Source extraction and provenance

`leanflow_cli/formalization/document_extraction.py` implements the extraction layer for
text, LaTeX, PDF-derived material, and parser-produced QA JSON. For structured QA
corpora it preserves labels, statements, optional reference solutions, declared
`uses`/dependencies, page information, source locators, crop boxes, and visual-audit
metadata.

The important contract is that source text remains the statement source of truth.
Natural-language solutions may help the prover, but they are hints rather than proof
fidelity constraints.

### 2. Whole-corpus planning

`leanflow_cli/formalization/corpus_planning.py` builds durable book-level planning
artifacts rather than treating each theorem as an unrelated prompt. The current plan
includes:

- a stable item inventory and source batches;
- source-declared foundations and dependencies;
- conservative mathematical concept extraction;
- typed dependency edges;
- a topological execution plan with source-order tie breaking;
- candidate shared-library structure; and
- reusable-concept and declaration-placement metadata.

The canonical corpus artifacts are:

```text
BookBlueprint.md
book-manifest.json
dependency-graph.json
reuse-registry.json
library-architecture.json
declaration-placement.json
campaign.json
```

### 3. A deliberately non-authoritative dependency graph

The current planner distinguishes two important classes of edge.

**Declared dependencies** are recorded as `declared_unverified`. They are treated as
hard scheduling constraints, but they are still explicitly marked unverified until the
formalization validates the corresponding Lean interface.

**Inferred `candidate` dependencies** are currently conservative retrieval/reuse hints,
for example from recurring concepts and nearby source structure. They are not silently
promoted into mathematical truth and do not become hard imports merely because a
heuristic suggested them.

This distinction is intentional. Reliable full-book dependency recovery is a core
research problem, not something the repository should claim is already solved.

### 4. Bottom-up, dependency-aware campaigns

`leanflow_cli/formalization/corpus_campaign.py` turns a corpus plan into a resumable
campaign. It tracks statement and proof stages separately and computes a ready
frontier from dependency status.

Hard predecessors must reach the required verified stage before a dependent batch is
selected. Soft/candidate predecessors influence reuse-aware prioritization without
blocking all progress. Within the available frontier, the scheduler can prefer cheaper,
less repeatedly-failed work rather than spending the entire budget on one local
blocker.

`corpus_campaign_runner.py` carries the campaign through durable attempts and outcomes,
while the rest of the LeanFlow workflow state provides checkpoints, activity logs,
research findings, and resumability.

### 5. Verified reuse and local theory growth

The corpus planner already has a conservative promotion policy for shared material:
promote a candidate only after it has two verified consumers or when it corresponds to
an explicit source-level definition. Domain-level shared modules are scaffolds, not
automatic imports; verified declarations are required before reuse is trusted.

This is meant to let the formalized book gradually become its own useful local library
instead of repeatedly reproving the same bridge facts in isolated theorem files.

### 6. Lean-first proof and verification engine

The existing LeanFlow proof workflows remain the execution engine underneath the
book-scale planner:

- declaration-by-declaration proof repair;
- Lean diagnostics and proof-state inspection;
- incremental LeanProbe checks with Lake as the final build gate;
- bounded Mathlib / semantic / local-code retrieval;
- research and theory-building lanes;
- resumable workflow state and failed-route memory;
- optional file-lock-aware multi-agent execution; and
- sandboxed runs that can export changes without touching the host working tree.

A run is not successful because the model produced plausible Lean. It is successful
only when the requested Lean scope verifies.

## The target node lifecycle

At book scale, it is useful to think of each mathematical node as moving through a
lifecycle like:

```text
extracted
  → normalized
  → dependency-resolved
  → statement-verified
  → proof-verified
  → reusable/promoted
```

Not every one of these names is a literal runtime enum today. They describe the system
boundary we want the harness to enforce: downstream automation should consume verified
artifacts whenever possible, and uncertainty should remain explicit rather than being
hidden inside generated code.

## What is still open

The current branch is an implementation scaffold for the full vision, not evidence that
whole-book autonomous formalization is solved. The main research problems include:

- **PDF-to-math fidelity.** Recovering mathematical items, notation, theorem boundaries,
  figures, references, and source foundations robustly across real books.
- **Dependency recovery.** Inferring hidden mathematical prerequisites rather than only
  literal source references or lexical concept overlap.
- **Edge verification and graph repair.** Converting candidate edges into verified Lean
  dependencies, rejecting false edges, discovering missing bridges, and handling cycles.
- **Statement fidelity.** Distinguishing a theorem that merely typechecks from one that
  faithfully represents the source statement.
- **Theory building.** Materializing intermediate Lean declarations when the desired
  mathematics is not available as a one-shot Mathlib theorem.
- **Shared-library architecture.** Deciding when a helper should remain local, become a
  domain module, or be generalized for broader reuse.
- **Long-horizon scheduling.** Allocating model calls and verification time across a DAG
  without repeatedly exhausting budget on a small set of blockers.
- **Dynamic replanning.** Updating readiness, complexity, dependencies, and priorities as
  verified declarations and new failure evidence arrive.
- **Whole-book closure.** Defining and measuring completion in terms of source coverage,
  statement fidelity, dependency closure, verified proofs, and explicit exclusions.

## Design principles

A few principles guide the current harness:

- **Lean verification is the gate.** Searches, model confidence, and token usage are not
  substitutes for verified declarations.
- **Source provenance stays attached.** A formal statement should remain auditable
  against the PDF/TeX/QA item from which it came.
- **Dependencies are typed and uncertainty is explicit.** Candidate edges are not treated
  as verified imports.
- **Build bottom-up.** Prefer nodes whose prerequisites are already available; let early
  verified foundations unlock later work.
- **Grow reusable theory, not a pile of isolated answers.** Successful bridge lemmas
  should reduce the cost of downstream formalization.
- **Failures are durable data.** Retrieval failures, statement blockers, proof blockers,
  infrastructure failures, and budget limits should inform later scheduling.
- **Long runs must resume.** Whole-book campaigns are too expensive and stateful to rely
  on one uninterrupted agent session.

## Relation to existing formalization workflows

The project is complementary to blueprint-driven Lean development. The Lean community's
`leanblueprint` tooling makes dependencies between human-written mathematical statements
explicit and connects blueprint nodes to Lean declarations. That is a useful model for
what a trustworthy dependency graph should expose.

ATLAS demonstrates that LLM-based textbook formalization can be scaled to many books and
provides a library/visualizer with informal statements, Lean code, and logical dependency
graphs. The focus of this harness is especially on the **process that produces and closes
such a graph autonomously**: source extraction, dependency recovery, ready-frontier
scheduling, theory building, Lean verification, reuse, budget control, and resumability.

Related projects:

- Lean Blueprint: <https://github.com/PatrickMassot/leanblueprint>
- Lean project template for blueprint-driven formalization: <https://github.com/leanprover-community/LeanProject>
- ATLAS: <https://github.com/facebookresearch/atlas-lean>

## Install

This branch retains LeanFlow's normal development and workflow interface.

```bash
git clone https://github.com/epfl-lara/LeanFlow.git
cd LeanFlow
./scripts/install-internal.sh
```

Verify the install:

```bash
leanflow --help
leanflow doctor
```

`doctor` checks the Lean toolchain, MCP backends, and external tools used by the
workflows, including `rg` and Poppler utilities for PDF processing.

## Quick start

Register an existing Lean project and run a workflow:

```bash
cd /path/to/lean-project
leanflow project init
leanflow workflow prove Main.lean
leanflow workflow formalize paper.tex
leanflow workflow formalize book.qa.json
```

Or use the interactive shell:

```bash
leanflow
```

```text
/prove [Main.lean]      /formalize docs/paper.tex      /autoformalize docs/
/goals   /diagnostics   /proof-state
/workflow status | activity | log 120
/skills   /provider   /doctor   /mcp status   /exit
```

From another terminal, `leanflow status` returns a bounded live summary. Use
`leanflow status --verbose` when you also want larger run history and a live sandbox
engine probe.

## What a proof run guarantees

A `prove` run is not done because the agent made a plausible edit. A successful run
ends with:

- the relevant Lean code building;
- clean diagnostics and no open goals;
- no `sorry` in the active target; and
- no remaining project `sorry` outside dependencies in the requested scope.

LeanFlow works in small verified steps. Failed proof attempts are recorded and the file
is kept buildable. Research findings remain advisory until they pass the same Lean
verification gates as foreground work.

Useful modes include:

```bash
leanflow workflow prove Main.lean --research
leanflow workflow prove Main.lean --clean-room
leanflow workflow prove Main.lean --human-review
leanflow workflow prove Main.lean --agents 3
```

Headless proof outcomes are explicit: `0` means verified, `3` means an authoritatively
promoted main-goal disproof, `2` means unresolved but checkpointed/resumable, `1` is a
startup/runtime failure, and `130` is a signal interruption.

## Workflows

- `prove` — repair and complete existing Lean proofs.
- `formalize` — turn a LaTeX/PDF source, TeX project, or structured QA corpus into
  statement-verified Lean declarations; proof workflows then fill the resulting
  obligations.
- `draft` — create Lean declarations and proof skeletons.
- `review` — inspect blockers, diagnostics, goals, and remaining `sorry`.
- `refactor` / `golf` — simplify existing Lean code without breaking verification.

`autoprove` and `autoformalize` remain compatibility aliases of `prove` and
`formalize`.

## Sandbox

Run a workflow in an isolated container when you do not want the agent editing the host
working tree directly:

```bash
./scripts/install-sandbox.sh
cd /path/to/lean-project
leanflow-sandbox workflow prove Main.lean
leanflow sandbox status
```

The sandbox exports the final diff as `changes.patch` under
`~/.leanflow/sandbox/runs/<run-id>/`.

## Providers and local runtimes

LeanFlow supports Codex OAuth, direct provider APIs, OpenAI-compatible endpoints, and
local runtimes such as vLLM, Ollama, and llama.cpp.

```bash
codex login
leanflow config set model.provider codex

leanflow models local start vllm google/gemma-3-27b-it
leanflow provider --requested local
```

An explicit workflow provider/model is launch-scoped and propagates to model-backed
auxiliary lanes for that run.

## Project state

LeanFlow separates user state from project workflow state:

- user config: `~/.leanflow/config.yaml`
- user env: `~/.leanflow/.env`
- project manifest: `.leanflow/project.yaml`
- project workflow state: `.leanflow/workflow-state/`

Workflow state stores activity, logs, checkpoints, file locks, route decisions,
failed-attempt history, research findings, project plans, and outcomes. Safe provider or
infrastructure pauses checkpoint current progress instead of discarding it.

## Repository map

The book-scale work is concentrated in the formalization layer:

```text
leanflow_cli/formalization/
  document_extraction.py       source / PDF / QA extraction
  corpus_planning.py           book manifest, DAG, execution plan, library architecture
  corpus_campaign.py           resumable dependency-aware campaign scheduling
  corpus_campaign_runner.py    durable statement/proof campaign execution
  corpus_reuse.py              reuse and shared-declaration machinery
  bounded_statement_refinement.py
                               bounded statement-generation/refinement loop
```

The broader execution engine lives under `leanflow_cli/lean/`,
`leanflow_cli/workflows/`, `core/`, and the workflow/skill specifications.

For deeper implementation details see:

- [Product reference](docs/product-reference.md)
- [Sandbox runtime](docs/sandbox-runtime.md)
- [Architecture](ARCHITECTURE.md)
- [Contributing / agent guide](AGENTS.md)

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Run the quality gate before committing:

```bash
black .
ruff check .
mypy
python -m pytest -q
```

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
