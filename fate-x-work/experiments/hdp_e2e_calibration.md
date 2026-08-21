# HDP E2E calibration protocol

## Metric boundary

The primary score is `agent_e2e_completed_batch_count / 366`. A batch receives
agent E2E credit only when successful statement and proof attempts both carry
`provenance: agent`. Kernel-checked artifacts from `manual_gold` remain useful for
diagnosis but never increase that score.

Campaign state after the first calibration:

- verified artifacts: 2/366 (`0.1`, `0.2`);
- agent E2E completions: 0/366;
- manual-gold completions: 2/366;
- paid agent attempts: two incomplete statement-generation attempts on `0.1`;
- paid campaign spend: `$7.09587`, beyond its `$6.50` remainder, so execution is
  not admitted without a new user budget decision.

## Isolation

- Agent targets live under `FateXWork/Questions`.
- Held-out calibration proofs live under `FateXWork/Gold/HDP` and build through
  the separate `FateXWorkGold` library.
- Campaign-launched agents receive clean-room path and module denials for the gold
  tree. File reads/searches/patches, Lean inspection/verification, and broad
  terminal scans fail closed.
- Verified shared declarations under `FateXWork/Questions/Shared` are admissible,
  but their provenance is recorded separately and never transfers completion
  credit to a consuming item.

## What the calibration established

The successful manual-gold proofs show that Questions 0.1 and 0.2 are supported by
the pinned Mathlib version. The principal observed failure was therefore not a
missing mathematical foundation: the paid formalizer accumulated a large search
transcript and hit its action limit before drafting declarations. The harness now
compacts semantic-search prose, reserves the estimated next-request cost before
sending, classifies campaign failures, and can recover locally verified artifacts
without mislabeling them as E2E agent results.

## Blind regression gate

For each calibration item, a valid blind regression must:

1. start from the clean agent target containing only its scaffold import;
2. run with `LEANFLOW_FORMALIZATION_PROVENANCE=agent` and gold clean-room guards;
3. produce an independently source-reviewed statement;
4. finish with zero `sorry` and a successful target/project Lean build;
5. append agent-provenance statement and proof outcomes to the campaign.

The zero-cost preflight for `0.1` passed the isolation/scaffolding portion and sent
no provider request. The paid generation/proof portion remains intentionally
unrun because the authorized pilot budget is exhausted.
