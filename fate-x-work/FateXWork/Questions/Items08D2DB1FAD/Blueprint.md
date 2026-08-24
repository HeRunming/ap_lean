# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Target Lean entry file: `FateXWork/Questions/Items08D2DB1FAD/Main.lean`
- Status: Lean declarations drafted and file-verified; approved by openai-codex verifier

## Source Statement Inventory

### 0.8

- Planned Lean declarations: `expected_norm_uniform_unitBall`
- Source qualifiers: ['The random vector is represented by a measurable function X from a probability space Ω into EuclideanSpace ℝ (Fin n).', 'Uniform distribution on the unit ball is expressed as equality of the pushforward measure with normalized Lebesgue volume restricted to that ball.']
- Scope changes: ['The Euclidean unit ball is formalized as Metric.closedBall 0 1. The boundary has Lebesgue volume zero, so this agrees with the usual uniform distribution convention.', 'Expectation is formalized as the Bochner integral ∫ ω, ‖X ω‖ ∂ℙ.']
- Statement verification status: approved by openai-codex verifier
- Source proof / prover notes: ['The theorem body is intentionally `by sorry` as requested.']

Source statement:

0.8 KK (Thin shell phenomenon, continued) Let $X$ be a random vector that is uniformly distributed in the Euclidean unit ball of $\mathbb{R}^n$. Prove that
$$
\mathbb{E}\|X\|_2 = \frac{n}{n+1}.
$$

Reference proof (optional hint):

0.8 Compute the CDF of $\| X \| _ { 2 } ,$ , deduce the probability density function by diferentiation, and then compute the expectation.
