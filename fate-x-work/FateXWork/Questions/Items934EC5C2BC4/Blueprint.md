# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Target Lean entry file: `FateXWork/Questions/Items934EC5C2BC4/Main.lean`
- Status: Lean declarations drafted and file-verified; approved by openai-codex verifier

## Source Statement Inventory

### 9.34

- Planned Lean declarations: `subadditive_sub_bound`
- Source qualifiers: ['V is represented as a real vector space via [AddCommGroup V] [Module ℝ V].', 'f is pointwise subadditive: ∀ x y : V, f (x + y) ≤ f x + f y.', 'The conclusion is quantified over all x, y : V.']
- Scope changes: none
- Statement verification status: approved by openai-codex verifier
- Source proof / prover notes: The theorem body is intentionally `by sorry`; no proof steps are included.

Source statement:

9.34 K (Subadditivity) Let $f : V \to \mathbb{R}$ be a subadditive function on a vector space $V$. Show that
$$
f(x) - f(y) \leq f(x - y) \quad \text{for all } x, y \in V.
$$

Reference proof (optional hint):

[not provided]
