# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Target Lean entry file: `FateXWork/Questions/Items111B8A9C24F/Main.lean`
- Status: Lean declarations drafted and file-verified; approved by openai-codex verifier

## Source Statement Inventory

### 1.11

- Planned Lean declarations: `kk_monotonicity_Lp_norm`, `kk_Lp_norm_inequality_not_reversible`
- Source qualifiers: ['Lp norms are represented by MeasureTheory.eLpNorm with exponents in ℝ≥0∞.', 'The ambient measure in part (a) is assumed to be a probability measure.', 'For part (b), the existential example is represented on ℕ with an existential probability measure and measurable real-valued random variable.']
- Scope changes: ['The informal random-variable condition is formalized as Measurable X.', 'The endpoint p = ∞ is represented by ∞ : ℝ≥0∞.']
- Statement verification status: approved by openai-codex verifier
- Source proof / prover notes: ['The theorem bodies are intentionally left as by sorry.']

Source statement:

1.11 KK (Monotonicity of the $L^p$ norm)

(a) Let $X$ be a random variable. Show that $\|X\|_{L^p}$ is an increasing function in $p$:
$$
\|X\|_{L^p} \leq \|X\|_{L^q} \quad \text{for any } 0 \leq p \leq q \leq \infty.
$$

(b) Demonstrate that the inequality in part (a) can not be reversed: for any $0 \leq p < q \leq \infty$, find an example of a random variable $X$ with $\|X\|_{L^p} < \infty$ and $\|X\|_{L^q} = \infty$.

Reference proof (optional hint):

1.11 Use Jensen inequality.
