# Formalization repair protocol

The statement lane treats compilation and source fidelity as separate gates.
The order below is the acceptance contract for HDP campaign work.

1. **Source admission.** Resolve the source item, cited declarations, target
   path, and dependency packet before a provider call. A missing reference or
   formula-free aside is a deterministic zero-cost outcome. It must not enter
   the statement retry queue.
2. **Statement contract.** The generator returns a Lean draft and a
   `source_contract` object. The contract records objects, domains,
   hypotheses, conclusion, and (for probability/time-process statements)
   measure space, measurability, integrability, and time domain. Run
   `statement_contract_lint` before remote Lean. It rejects tautological
   definitions and a theorem whose conclusion is repeated as a binder
   hypothesis.
3. **Fresh generation and review.** A retry receives the numbered findings
   from the previous attempt; persisted findings are injected into the
   fresh-process prompt even when no evidence file exists. The generator must
   produce a materially changed
   contract and candidate. The independent reviewer returns exactly `PASS` or
   `BLOCK`; `PASS` requires matching objects, domains, quantifiers,
   hypotheses, conclusion, and edge cases. Retrieval planning is an optional
   hint stage: it uses a short 90-second deadline and at most 256 output
   tokens. A transient timeout, connection failure, or HTTP 504/524 records
   `planner_unavailable` and continues with an empty retrieval context.
   Quota, authentication, and provider-resolution failures still stop the
   action; the unresolved quota reservation remains an `unknown` hold and is
   never retried by the SDK.
4. **Remote Lean check.** Compile only after source admission and contract lint.
   The authoritative check runs in the unique remote workspace
   `/data/hrm/fate-x-work`; a warm probe may screen candidates but cannot grant
   acceptance. Compilation success never overrides a semantic `BLOCK`.
5. **Retry and escalation.** Classify failures as source context, semantic
   contract, semantic review, Lean compilation, or infrastructure. Retry only
   the relevant stage. Repeated identical semantic findings or an unchanged
   contract escalates to a fresh review rather than extending one self-repairing
   conversation.
6. **Acceptance.** A statement is complete only when semantic review passes,
   the remote project build passes, the target is reachable from the project
   root, and the campaign ledger records the review evidence. Proof filling is
   a later stage.

The following cases are useful regression fixtures: `p207.x1` for tautological
and circular semantics, `p206.x1` for retry/escalation accounting, and
`items-6.10` for missing source context. Run the focused checks with:

```bash
./.venv/bin/pytest -q tests/leanflow/test_bounded_statement_refinement.py \
  -k 'statement_contract_lint or source_reference or statement_scope_preflight'
```
