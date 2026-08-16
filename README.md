# AP Lean research workspace

This repository is a portable snapshot of the FATE-X / LeanFlow automatic
formalization investigation conducted on 2026-08-16.

## Current result

FATE-X Problem 3 is completely formalized without `sorry` or `admit`.

```bash
cd fate-x-work
lake update
lake build FATEX.«3_Component» FATEX.«3_Blueprint» FATEX.«3»
```

The proof factors through double-coset components, subgroup orbits,
orbit--stabilizer, equality of relative indices for conjugate finite-index
subgroups, fiberwise equivalences, and finally a compatible equivalence between
left and right cosets.

Relevant files:

- `fate-x-work/FATEX/3_Component.lean`: component/fiber mathematics.
- `fate-x-work/FATEX/3_Blueprint.lean`: Hall condition and transversal reconstruction.
- `fate-x-work/FATEX/3.lean`: original benchmark theorem, now solved.
- `fate-x-work/experiments/problem3_diagnosis.md`: experiment and failure analysis.

## LeanFlow changes

`leanflow/` is the working source snapshot used in the experiment. The main
new search behavior is a bounded direct fallback to `https://leansearch.net`
for explicit `natural-language` searches. It does not depend on Lean LSP MCP,
returns at most eight results, and truncates statement/description payloads to
avoid context blow-up.

Focused verification:

```bash
cd leanflow
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
black --check leanflow_cli/lean/lean_search_providers.py \
  leanflow_cli/lean/lean_services.py tests/leanflow/test_lean_services.py
ruff check leanflow_cli/lean/lean_search_providers.py \
  leanflow_cli/lean/lean_services.py tests/leanflow/test_lean_services.py
python -m pytest tests/leanflow/test_lean_services.py -q -n 0
```

At snapshot time the focused suite passed (`103 passed`). Mypy was blocked by
an existing Python-version/NumPy-stub configuration mismatch, not by the new
search code.

## External component

The inspected LeanSearch implementation is:

- <https://github.com/frenzymath/LeanSearch>
- commit `d461a7669842bd70dc84670e0bdce27c2fe478bd`

Clone it separately if local indexing/server work is needed:

```bash
git clone https://github.com/frenzymath/LeanSearch.git
cd LeanSearch
git checkout d461a7669842bd70dc84670e0bdce27c2fe478bd
```

LeanSearch correctly retrieves the existing Mathlib Hall theorem, but did not
retrieve a ready-made double-coset component-counting theorem. That negative
result motivated the theory-building decomposition used in Problem 3.

## Recommended next work

1. Extract the successful Problem 3 workflow into a LeanFlow
   `theory-building` planner: natural-language grounding, explicit bridge-node
   declarations, and mandatory `.olean` validation per node.
2. Treat successful verified declarations, rather than searches or token use,
   as the progress metric.
3. Add bounded decomposition timeouts and preserve partial structured output.
4. Run the same diagnostic workflow on a representative sample of FATE-X,
   classifying failures as retrieval, missing-library bridge, statement
   translation, or proof-search failures.

## Snapshot hygiene

Virtual environments, `.lake` build products, caches, LeanFlow runtime state,
and logs are intentionally excluded. No API keys or local credential files are
included.
