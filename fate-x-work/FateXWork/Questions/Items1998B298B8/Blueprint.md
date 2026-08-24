# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Target Lean entry file: `FateXWork/Questions/Items1998B298B8/Main.lean`
- Status: Lean declarations drafted and file-verified; approved by openai-codex verifier

## Source Statement Inventory

### 1.9

- Planned Lean declarations: `dense_random_graphs_no_isolated_vertices`
- Source qualifiers: ['The friendship graph on n freshmen is represented by X n : Ω → SimpleGraph (Fin n).', 'Independent friendship edges with common probability p n are encoded by the full Erdős–Rényi product probability assigned to every finite graph G.', 'The no-friendless-student event is expressed as ∀ v : Fin n, ∃ w : Fin n, (X n ω).Adj v w.']
- Scope changes: ['The density assumption is restricted to positive n, since the source concerns graphs with a positive number of freshmen and the expression log n / n is not intended at n = 0.', 'Measurability is required explicitly for every graph fiber {ω | X n ω = G}, ensuring that the displayed Erdős–Rényi fiber probabilities are genuine probabilities.']
- Statement verification status: approved by openai-codex verifier
- Source proof / prover notes: ['The theorem body is intentionally `by sorry`.', 'The exact product law over graph fibers encodes the independent-edge model rather than assuming only a derived isolated-vertex probability.']

Source statement:

1.9 K (Dense random graphs have no isolated vertices) Let us refine the result of Example 1.4.2. Suppose $n$ freshmen arrive on campus, with each pair becoming friends independently with probability $p_n$. Fix any $\varepsilon > 0$ and assume that
$$
p_n > \frac{(1 + \varepsilon)\ln n}{n} \quad \text{for every } n \in \mathbb{N}.
$$
Prove that there are no friendless students with probability that converges to 1 as $n \to \infty$.

Reference proof (optional hint):

1.9 Following the proof of the result in Example 1.4.2, the problem reduces to checking that n $\bar { \cdot } ( 1 - p _ { n } ) ^ { n - 1 } \dot {  } 0$
