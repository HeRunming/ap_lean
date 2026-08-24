# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Target Lean entry file: `FateXWork/Questions/Items1143C9DEB84/Main.lean`
- Status: Lean declarations drafted and file-verified; approved by openai-codex verifier

## Source Statement Inventory

### 1.14

- Planned Lean declarations: `expectation_finset_rpow_bounds`
- Source qualifiers: ['The exponent p is represented by a real number with hypothesis 1 ≤ p; its finiteness is automatic for real p.', 'The family is indexed by Fin n and includes the source convention that the family is nonempty through hn : 0 < n.', 'Random variables are required to be measurable through hX_measurable.']
- Scope changes: ['Random variables take values in ENNReal, whose codomain encodes nonnegativity.', 'Expectations are represented by the extended nonnegative integral ∫⁻, so the statement retains cases with infinite expectations rather than imposing integrability assumptions.', 'All powers are represented by ENNReal.rpow.']
- Statement verification status: approved by openai-codex verifier
- Source proof / prover notes: ['The theorem body is intentionally left as by sorry.']

Source statement:

1.14 KK Let $X_1, \ldots, X_n$ be nonnegative random variables. Prove that for any $1 \leq p < \infty$, we have
$$
\left(\sum_{i=1}^{n} (\mathbb{E} X_i)^p\right)^{1/p}
\leq \mathbb{E} \left(\sum_{i=1}^{n} X_i^p\right)^{1/p}
\leq \left(\sum_{i=1}^{n} \mathbb{E} \left(X_i^p\right)\right)^{1/p}.
$$

Reference proof (optional hint):

1.14 Both bounds follow from Jensen inequality. For the first bound, use (1.19) for the for the random vector $X \ = \ ( X _ { 1 } , \ldots , X _ { n } )$ and the $\ell ^ { p \ }$ norm. For the second bound, consider the convex function $\phi ( x ) = x ^ { p }$
