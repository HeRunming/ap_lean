# Formalization Blueprint: HDP Question 0.2

- Source: `HDP/source/full/qa/questions.json`, item `0.2`, PDF page 14.
- Gold target: `FateXWork/Gold/HDP/Items02E620D532/Main.lean`.
- Provenance: `manual_gold`; excluded from agent E2E success metrics.
- Status: source-faithful statements drafted; proofs pending.

## Source Fidelity

The source asks for `E ‖Z-EZ‖² = min_a E ‖Z-a‖²` for a square-integrable random
vector in `ℝⁿ`. `expectation_minimizes_meanSquaredError` represents the minimum as
`IsLeast` of the range of all constant-center risks, preserving both attainment and
the universal lower bound. The supporting theorem `meanSquaredError_sub_variance`
formalizes the supplied solution identity exactly.

## Reuse

- Imports the verified result for 0.1(a).
- Reuses `FateXWork.Questions.Shared.RealN`.
- The decomposition theorem should be promoted if later least-squares questions consume it.
