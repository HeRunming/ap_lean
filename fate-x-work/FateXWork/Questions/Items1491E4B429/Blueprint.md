# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Target Lean entry file: `FateXWork/Questions/Items1491E4B429/Main.lean`
- Status: Lean declarations drafted and file-verified; approved by openai-codex verifier

## Source Statement Inventory

### 1.4

- Planned Lean declarations: `maximum_principle`
- Source qualifiers: ['The function is convex on all of ℝ^n, represented by ConvexOn ℝ Set.univ f.', 'The subset T is represented as a Set (Fin n → ℝ).', 'The supremum is expressed using sSup over the images of convexHull ℝ T and T.']
- Scope changes: ['Euclidean space ℝ^n is represented as Fin n → ℝ.', 'A general convex function is formalized as a function convex on Set.univ.']
- Statement verification status: approved by openai-codex verifier
- Source proof / prover notes: ['The theorem body is intentionally left as by sorry.', 'Mathlib is imported to provide the real-number algebraic and supremum instances required by ConvexOn and sSup.']

Source statement:

1.4 K (A maximum principle) Prove for any convex function $f$ and a subset $T \subset \mathbb{R}^n$:
$$
\sup_{x \in \operatorname{conv}(T)} f(x) = \sup_{x \in T} f(x).
$$

Reference proof (optional hint):

1.4 To prove the upper bound, express a point x ∈ conv(T) as a convex combination of some points in $T$ and use Jensen inequality from Exercise 1.3.
