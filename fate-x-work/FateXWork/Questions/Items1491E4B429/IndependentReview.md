# Independent statement/source review

Verdict: PASS

Provider: `openai-codex`

Reviewer response:

PASS

The Lean statement faithfully formalizes the claim over \( \mathbb{R}^n \): `Fin n → ℝ` is the intended vector space, `ConvexOn ℝ Set.univ f` means globally convex, and `convexHull ℝ T` is the real convex hull. Both sides take the supremum of the image of the corresponding set under the same real-valued function.

Edge cases are preserved: empty `T`, `n = 0`, and unbounded images occur identically on both sides as far as the equality is concerned. No prior semantic risks were listed, so none require remediation.
