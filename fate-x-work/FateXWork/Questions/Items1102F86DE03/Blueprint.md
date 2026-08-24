# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Target Lean entry file: `FateXWork/Questions/Items1102F86DE03/Main.lean`
- Status: Lean declarations drafted and file-verified; approved by openai-codex verifier

## Source Statement Inventory

### 1.10

- Planned Lean declarations: `sparse_random_graphs_have_isolated_vertices`
- Source qualifiers: ['The edge-probability sequence satisfies 0 ≤ p n ≤ 1 for every n.', 'The sparse-regime upper bound is imposed eventually along n → ∞.', 'Each A n i is the measurable event that student i is friendless.', 'X n i is the real-valued indicator of A n i.', 'The single-vertex and distinct-pair friendlessness probabilities are specified explicitly.']
- Scope changes: ['The source bound stated for every n was replaced by an eventual-atTop bound. This avoids the inconsistency at n = 1, where log 1 = 0 would force p 1 < 0 despite 0 ≤ p 1.', 'Measurability assumptions for the friendless events were added so that the indicator random variables and their expectations/variances represent the intended probabilistic objects.']
- Statement verification status: approved by openai-codex verifier
- Source proof / prover notes: The theorem body is intentionally `by sorry`; no proof steps are supplied.

Source statement:

1.10 KKKK (Sparse random graphs have isolated vertices) Let us prove a converse to Exercise 1.9. Fix any $\varepsilon > 0$ and assume that
$$
p_n < \frac{(1 - \varepsilon) \ln n}{n} \quad \text{for every } n \in \mathbb{N}.
$$
Then there exists at least one friendless student with probability that converges to 1 as $n \to \infty$. You will prove this result using the so-called second moment method:

(a) Denote the number of friendless students by $S_n$ and express it as $S_n = X_1 + \ldots + X_n$ where $X_i$ is the indicator of the event that student $i$ is friendless. Show that
$$
\mu_n = \mathbb{E} S_n \to \infty.
$$
Thus the expected number of friendless students is large. But this does not automatically imply that there exists even one friendless student with high probability! (Why?)

(b) Compute the second moment $\mathbb{E} S_n^2$ by expanding the square. Conclude that
$$
\frac{\mathrm{Var}(S_n)}{\mu_n^2} \to 0.
$$

(c) Use Chebyshev inequality to complete the proof.

Reference proof (optional hint):

1.10 (b) Expanding yields E $\begin{array} { r } { { \bf \chi } _ { : S _ { n } ^ { 2 } } = \sum _ { i = 1 } ^ { n } \mathbb { E } X _ { i } ^ { 2 } + \sum _ { i \neq j } \mathbb { E } X _ { i } X _ { j } } \end{array}$ . Interpret each term E $X _ { i } X _ { j }$ as the probability that both students i and $j$ are friendless. Compute this probability.
