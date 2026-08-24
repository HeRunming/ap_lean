# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Target Lean entry file: `FateXWork/Questions/Items15077AFA8F/Main.lean`
- Status: Lean declarations drafted and file-verified; approved by openai-codex verifier

## Source Statement Inventory

### 1.5

- Planned Lean declarations: `cube_eq_convexHull_vertices`
- Source qualifiers: ['The cube is represented as the set of functions Fin n → ℝ whose coordinates lie in [-1, 1].', 'The vertices are represented as functions Fin n → ℝ whose coordinates lie in {-1, 1}.', 'The convex hull is taken over ℝ.']
- Scope changes: ['Opened the Set namespace.', 'Imported Mathlib.Data.Real.Basic to provide the ordered field structure and numeral instances for ℝ.']
- Statement verification status: approved by openai-codex verifier
- Source proof / prover notes: ['The theorem body is intentionally left as by sorry.', 'The retrieved convex-hull interfaces are relevant to a future proof but are not needed in the statement.']

Source statement:

1.5 KK (Expressing a cube as a convex hull of its vertices) It seems almost obvious that the cube is the convex hull of its vertices:
$$
[-1, 1]^n = \operatorname{conv}(\{-1, 1\}^n).
$$
Prove this by expressing any point in the cube as a convex combination of the vertices.

Reference proof (optional hint):

[not provided]
