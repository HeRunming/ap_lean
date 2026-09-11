# HDP remote sync isolation acceptance — 2026-09-09

The three compilation sync entrypoints (`remote-bin/lake`, the warm probe,
and the SSH terminal backend) previously copied the local project tree without
excluding host configuration and campaign ledgers. A later compile could
therefore restore stale local files over repaired remote state.

All three now use `core/remote-project-sync.exclude`. The policy excludes
runtime homes, `.env*`, Codex/Elan configuration, Git/Lake/venv state, root
`bin/` and `remote-bin/`, and campaign JSON ledgers, locks, state, temporary
files, and backups at every depth. Lean sources, statement candidates,
blueprints, source JSON, `lakefile.*`, `lake-manifest.json`, and `lean-toolchain`
still transfer. Candidate cleanup remains restricted to the exact candidate
paths supplied to that compile invocation. Missing policy files stop rsync
before it changes the destination.

The local `/Users/blackbox/m2f/hdp-run` and current local LeanFlow runner are
the sole scheduling entrypoint for subsequent work. The remote ledger retains
prior execution evidence and must not be automatically overwritten by local
sync. Runtime configuration deployment and ledger reconciliation are separate,
explicitly scoped operations. Lean compilation remains exclusively under
`/data/hrm/fate-x-work` on the remote host.

Local validation: 32 tests passed; 11 SSH integration tests were skipped. The
tests capture the actual wrapper argv and replay its rsync filters between
temporary local directories, confirming preserved state and transferred Lean
sources/candidates. Ruff, targeted Black, `git diff --check`, and `bash -n`
passed. No local Lean, paid provider, or campaign was run; no Git commit was
created.

Changed files:

- `core/remote-project-sync.exclude`
- `remote-bin/lake`
- `leanflow_cli/formalization/remote_warm_probe.py`
- `tools/environments/ssh.py`
- `tests/leanflow/test_remote_lake_wrapper.py`
- `tests/tools/test_ssh_environment.py`
- `pyproject.toml` (include the shared policy in installed package data)
- `ARCHITECTURE.md`
- `docs/hdp-operations.md`
- This acceptance report.

Live remote Lean acceptance is tracked separately in
`/Users/blackbox/m2f/.leanflow-home/logs/hdp-readiness-20260909T085411Z/`.
The directory contains the exact test driver, UTC events, compiler logs, and
before/after configuration and ledger hashes; secrets are not logged.

Live acceptance completed at 2026-09-09 09:04:51 UTC: two distinct
`StatementCandidate_*.lean` files, each importing Mathlib and proving a Nat
identity with `simp`, passed the remote wrapper with exit code 0. The first
compile took 451.320 seconds during cold disk-page reads; the second took
6.925 seconds. Six remote file hashes (including runtime configuration,
launcher, and campaign ledger) and the local ledger hash were unchanged.
No further warmup or recompilation was needed.

The initial `report.json` records one residual candidate: the test driver had
kept its first local candidate until both compiles ended, so the second sync
uploaded it again after remote cleanup. Only this run's two exact UUID paths
were then deleted, and a separate `cleanup-receipt.json` preserves the final
absence checks. The driver now unlinks each local candidate in that compile's
`finally` block. The original report and event log remain unchanged.
