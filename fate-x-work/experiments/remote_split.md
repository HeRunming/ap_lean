# Local-agent / remote-Lean execution

The model provider and LeanFlow orchestrator run locally. Terminal and file
tools use the SSH backend and operate in `/home/hrm/ap_lean/fate-x-work`.

Before a run:

```bash
source ../leanflow/.venv/bin/activate
source ../leanflow/.env.remote-lean
```

Benchmark runs must start from task-specific managed state. Reusing a shared
`.leanflow/workflow-state` can inject the goal and graph of an earlier FATE-X
problem into a later run. Archive the prior state before changing benchmark
targets, and retain it with the experiment artifacts.

The remote tree must contain the same benchmark source files as the local
project. LeanFlow's file tools and Lean verifier both operate on the remote
tree; after an experiment, synchronize changed source files back locally before
committing them.
