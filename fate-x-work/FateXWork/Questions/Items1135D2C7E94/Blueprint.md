# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Target Lean entry file: `FateXWork/Questions/Items1135D2C7E94/Main.lean`
- Status: Lean declarations drafted and file-verified; approved by openai-codex verifier

## Source Statement Inventory

### 1.13

- Planned Lean declarations: `expectation_iSup_bounds`, `expectation_iSup_optimal_examples`, `independent_expectation_iSup_approximately_optimal`
- Source qualifiers: ['All random variables are modeled as measurable functions into ℝ.', 'Nonnegativity is stated pointwise for every indexed random variable.', 'A probability-measure assumption is included for each probabilistic setting.', 'Integrability assumptions ensure that the real-valued expectations are finite.', 'The absolute constant in part (c) is represented by coeff : ℝ with 0 < coeff.', 'Independence in part (c) is represented by ProbabilityTheory.iIndepFun.']
- Scope changes: ['The finite family X₁, ..., Xₙ is indexed by Fin n.', 'The notation max over indices is represented by the finite supremum ⨆ i : Fin n, ... .', 'Expectations are represented by the Bochner integral ∫ ω, ... ∂μ.']
- Statement verification status: approved by openai-codex verifier
- Source proof / prover notes: ['All theorem bodies are placeholders of the form by sorry.', 'No proof steps are included.']

Source statement:

1.13 KKK (Expectation of a maximum) Let $X_1, \ldots, X_n$ be nonnegative random variables.

(a) Prove that
$$
\max_{i \le n} \mathbb{E} X_i \le \mathbb{E} \max_{i \le n} X_i \le n \cdot \max_{i \le n} \mathbb{E} X_i.
$$

(b) Demonstrate that both inequalities in part (a) may be optimal. Specifically, find random variables $X_1, \ldots, X_n$ satisfying $\max_i \mathbb{E} X_i = \mathbb{E} \max_i X_i > 0$ and random variables $Y_i$ satisfying $\mathbb{E} \max_i Y_i = n \cdot \max_i \mathbb{E} Y_i > 0$.

(c) Demonstrate that the upper bound in part (a) may be approximately optimal even for independent random variables. Specifically, find independent random variables $X_1, \ldots, X_n$ satisfying $\mathbb{E} \max_i X_i > cn \cdot \max_i \mathbb{E} X_i$, where $c > 0$ is an absolute constant.

Reference proof (optional hint):

1.13 (a) To prove the first inequality, use Jensen inequality (1.19) for the random vector $X =$ $( X _ { 1 } , \ldots , X _ { n } )$ . Guess which norm you should use here. To prove the second inequality, bound the maximum of n nonnegative numbers by the sum. (c) Consider independent Bernoulli random variables Ber $\left( p _ { n } \right)$ ; find the value of $p _ { n }$ that make the argument work.
