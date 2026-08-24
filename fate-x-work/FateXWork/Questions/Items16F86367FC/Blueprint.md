# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Target Lean entry file: `FateXWork/Questions/Items16F86367FC/Main.lean`
- Status: Lean declarations drafted and file-verified; approved by openai-codex verifier

## Source Statement Inventory

### 1.6

- Planned Lean declarations: `l1UnitBall`, `signedStandardBasis`, `crossPolytopeVertices`, `crossPolytopeCoeff`, `l1UnitBall_eq_convexHull_crossPolytopeVertices`, `l1UnitBall_explicit_convex_combination`
- Source qualifiers: ['The ambient space is represented as `Fin n → ℝ`.', 'The ℓ¹ unit ball is represented by the condition `∑ i, |x i| ≤ 1`.', 'The vertices are represented as the signed standard basis vectors `±e_i`.']
- Scope changes: ['A hypothesis `hn : 0 < n` is included in the convex-hull equality because the stated equality fails in dimension zero: the ℓ¹ unit ball in `Fin 0 → ℝ` is nonempty, while the convex hull of the empty vertex set is empty.', '`crossPolytopeCoeff` is marked `noncomputable` because its formula uses real division.']
- Statement verification status: approved by openai-codex verifier
- Source proof / prover notes: ['All theorem bodies are placeholders of the form `by sorry`.', 'The coefficient formula assigns positive and negative parts of each coordinate to the corresponding signed basis vertices, with the remaining mass split equally between the two vertices indexed by `anchor`.']

Source statement:

1.6 KK (Expressing a cross-polytope as a convex hull of its vertices) Check that the unit ball corresponding to the $\ell^1$ norm in $\mathbb{R}^n$ is the absolute convex hull of the standard basis $e_1, \ldots, e_n$ in $\mathbb{R}^n$, that is
$$
B_1^n = \operatorname{conv}(\{\pm e_1, \ldots, \pm e_n\}).
$$
Write down a formula that expresses any point $x \in B_1^n$ as a convex combination of the vectors $\pm e_1, \ldots, \pm e_n$.

Reference proof (optional hint):

[not provided]
