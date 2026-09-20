# HDP operational controls

## Snapshot layout

This branch archives the harness for the whole HDP book, including `hdp-run`,
`hdp-scale`, and `hdp-long-run`. The launchers retain the operator's deployment
defaults: `/Users/blackbox/m2f/leanflow` contains this package, the three
launchers are deployed in `/Users/blackbox/m2f`, and `fate-x-work` is their
sibling Lean project. Review these paths, SSH settings, credential fingerprints,
and quota paths before deploying elsewhere. The archived scale launcher imports
the adjacent package when tested directly from this repository.

Credentials, campaign state, generated book proofs, provider logs, and quota
evidence are separate runtime inputs and are not part of this code snapshot.
The test suite uses synthetic quote fixtures and does not need operator logs.

## Mixed workers and bounded recovery

`hdp-scale --stage mixed --workers 4` shares four worker slots between proof
and statement actions. It favors the less occupied lane and uses the other
lane when no eligible work is available. Proof actions retain target,
import-reachability, and dependency checks. The shared checkout uses one
Lean-heavy slot. `--max-wall 180` stops new dispatch after three hours;
already running children drain under their existing action timeouts.
All children share one quota ledger, including outstanding request holds.

`hdp-long-run` supervises mixed four-worker waves under one fixed wall-clock
deadline (at most eight hours). The existing per-wave infrastructure fuse stays
enabled. After all workers drain, a fuse trip is retryable only when every
infrastructure receipt identifies an allowed transient provider failure, including
HTTP 500/502/503/504/524 responses and auxiliary request deadlines. Unknown
errors, leaked leases, source changes, authentication errors, and halted quota
ledgers stop the supervisor. There are
at most three cooldowns across the whole run: five, ten, then twenty minutes.
Cooldowns count toward the same deadline; they never extend the run.

An action without a receipt can be deferred only after all workers drain and
reconciliation verifies its structured exit-124 result, matching worker/stage/batch
identity, released lease, unchanged target-directory Lean sources, unchanged
attempt ledger, and idle local and remote runtimes. Log text alone is not
rollback evidence. A safely deferred batch is excluded for the rest of this
supervisor run; failed checks stop it. `--defer-batch` also excludes a known
problem batch explicitly. Unknown quota holds remain reserved.

For the September 18 operator authorization, this wrapper uses the remaining
existing ledger first. Only a local quota-reservation shortfall activates one
new 10,000,000-quota allocation linked to the immutable old ledger. Provider
balance errors and reservation overruns do not activate it. Unknown holds are
never released. The outer `status.json` records each drained wave, the absolute
deadline, cooldowns, and whether the additional allocation was activated.
The long supervisor can be stopped with SIGTERM (the active wave drains).
Its log directory's `STOP` file is also propagated to the active wave to stop
new dispatch, and prevents subsequent waves from starting.

The HDP campaign runner supports a non-executing reconciliation pass:

```bash
python -m leanflow_cli.formalization.corpus_campaign_runner \
  /path/to/campaign.json --project-root /path/to/hdp-project --reconcile-only
```

`--reconcile-only` re-checks completed proof receipts, downgrades stale rows to
`proof_retry`, persists `last_reconciliation_audit`, and prints counts and
reason categories. It never removes or rewrites Lean source files and performs
no provider calls or budget reservations.

Verification reviews accept the existing explicit timeout argument and the
`LEANFLOW_ADVISORY_VERIFICATION_TIMEOUT_S` environment setting. Deadlines are
clamped to 5–3600 seconds. Transient gateway retries are controlled by
`LEANFLOW_AUXILIARY_RETRY_COUNT` (0–3, default 2) and
`LEANFLOW_AUXILIARY_RETRY_BACKOFFS` (up to three non-negative delays, each at
most 30 seconds). Review telemetry and token/cost accounting remain attached
to the same request/result activity records.

The opt-in remote warm service accepts `--warmup-workers` (or
`LEANFLOW_REMOTE_WARMUP_WORKERS`) with a bounded range of 1–4. Each service
uses one temporary remote directory, cleans it on exit, and keeps final
acceptance on the checked-in remote `lake` wrapper; warm checks are only a
screening accelerator.

Remote compilation synchronizes Lean sources and Lake metadata, including
temporary statement candidates. It must not deploy host configuration or
campaign state. The Lake wrapper, warm probe, and SSH terminal sync all load
`core/remote-project-sync.exclude`: runtime homes, `.env*`, root `bin/` and
`remote-bin/`, campaign ledgers, locks, state, and backups stay untouched on
the destination. Rules apply at every book/batch depth; source JSON remains
eligible. Missing exclusion rules fail the sync before compilation. Keep this
file beside the installed `core` package when deploying the wrapper.

Campaign transfers and runtime configuration updates require a separate,
explicitly scoped operation. A compile or warmup must never be used to restore
them from another host's checkout. Candidate cleanup remains limited to the
exact `StatementCandidate_*.lean`/`ZeroCostCandidate_*.lean` paths passed to
that invocation; it never removes another worker's candidates or `Main.lean`.

## Raw provider quota admission

The finite scale controller uses a separate `provider_quota` budget, initially
2,000,000 units for at most 24 items. It does not relabel unknown campaign USD
costs as paid dollars. `agent/accounting/provider_quota.py` loads an exact
endpoint/model/group quote, requires a pricing version and an observation
timestamp no older than 24 hours, and refuses missing or malformed quotes.
`provider_quota_budget.py` owns the independent atomic ledger. Enable it through
`LEANFLOW_PROVIDER_QUOTA_BUDGET_PATH` and
`LEANFLOW_PROVIDER_QUOTA_QUOTE_PATH`; request hooks may pass their explicit
child environment instead of relying on ambient environment state.

Each provider call reserves an input bound based on UTF-8 bytes plus framing
headroom and the request's output token cap. The default estimate ignores
cache discounts and rounds up. Successful responses with usage are charged
that conservative estimate. Timeouts, exceptions, and missing usage retain
the whole hold; reported usage above the reservation stops new calls. File
locks protect the shared state across threads and independent processes.
Both auxiliary retry counts must be zero so one request hold cannot hide paid
transport retries. These are conservative quota estimates; a changed route,
provider-added input, or changed price can exceed them, so they are not a
guarantee about actual provider billing.

For explicitly authorized long runs where gateway failures leave usage unknown, pass
`hdp-scale --ignore-unknown-holds`. This wave-level opt-in records
`quota_accounting_mode=success_only`: successful usage-bearing requests still charge
actual quoted usage, while `unknown` reservations remain immutable evidence and are
reported as `unknown_hold_quota`; only their holds are excluded from admission. The
default remains `conservative`, which counts every unknown hold. Run metadata and
`events.jsonl` record the selected mode.

The read-only zcloud evidence is stored in
`/Users/blackbox/m2f/.leanflow-home/logs/zcloud-pricing-20260909T091106Z/`.
The snapshot's `quota-quote.json` binds `https://api.zcloudapi.com/v1`,
`gpt-6-astra`, and the observed `GPT 蒸馏分组` multiplier 1.6. Of 1,000
returned token log rows, all 749 nonzero Astra charges exactly matched:

```text
round(((prompt_tokens - cache_tokens) + cache_tokens * 0.1
       + completion_tokens * 5) * 0.6849315068493151 * 1.6)
```

The two 49-input/5-output smoke requests each charged 81 quota. Public
`/api/status` reports `quota_per_unit=500000` and exchange rate 7.3, while
the site's front-end overrides the display to a USD label and multiplies
quota/500000 by 7.3. The compatibility billing endpoint follows this display
conversion, so its `_usd` field names do not establish a safe external-dollar
conversion. Actual per-request reconciliation should use the authenticated
`/api/log/token` receipt's `request_id` and `quota`, keeping provider units.

`/api/usage/token/` returns `total_used` and `total_available` for the key.
The observed available balance exceeded 262 million quota before this finite
2-million-quota run. The key also carries other Codex traffic: account-wide
deltas are not attributable to one batch. This first finite run does not add
periodic balance polling; a shared-account balance floor remains a follow-up.
Another observation item is whole-tree candidate sync: concurrent candidate
deletion can race another worker's rsync or recreate a just-cleaned remote
candidate. Diagnose actual rsync exit 24 or residue before changing sync;
never remove other workers' candidate files.

Quota unit tests: 21 passed, including independent-process contention,
unknown holds, overrun stopping, scope mismatch, and stale/malformed quote
rejection. Mypy passed for both modules; targeted Ruff/Black and diff checks
passed. This validation used no model requests or Lean compilation. A statement
review PASS is reported as an accepted declaration; it does not imply the
declaration is reachable from the book root or that its proof is complete.

### Isolated wave budget

When a halted wave is followed by a separately authorized allocation, create a
fresh ledger and retain the halted ledger as immutable evidence. Do not use the
halted-ledger import path for this case, because that path intentionally copies
historical charges and holds. From the local HDP checkout, run the offline
creation command with a new destination, the exact source ledger, an operator
authorization reference, and a timezone-qualified authorization time:

```bash
./hdp-scale \
  --create-isolated-quota-budget \
  --isolated-from-quota-budget fate-x-work/.leanflow/scale-budget.json \
  --quota-budget fate-x-work/.leanflow/wave4-scale-budget.json \
  --quota-limit 2000000 \
  --quote .leanflow-home/logs/zcloud-pricing-20260909T091106Z/quota-quote.json \
  --operator-authorization-ref wave4-approval-YYYYMMDD \
  --operator-authorized-at 2026-09-10T08:00:00+00:00
```

This command performs no provider request, campaign mutation, or Lean build. It
verifies the exact provider/model quote before atomically writing the new ledger.
The new ledger starts with zero charges, zero reservations, and the specified
raw quota limit. It records the source ledger SHA-256, its charged/held/
unknown summary, the authorization metadata, and quote metadata. Both source
and destination must remain inside the local HDP checkout, and the destination
must not already exist. Subsequent execution can select the new ledger with
`--quota-budget fate-x-work/.leanflow/wave4-scale-budget.json`; the source ledger
must remain untouched.

After an infrastructure stop, inspect both the provider diagnostics and the
native proof finalizer. Shutdown uses a cooperative tool interrupt to quiesce
owned writers; after successful shutdown, the runner clears only its own
internal interrupt before the terminal kernel check. Earlier cancellation,
new user interrupts, and failed shutdown remain blocking. A saved proof whose
final check was interrupted is not a completed proof. Reverify it through an
existing acceptance path; operator verification is recorded separately from
automatic agent recovery.
