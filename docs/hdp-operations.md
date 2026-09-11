# HDP operational controls

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
