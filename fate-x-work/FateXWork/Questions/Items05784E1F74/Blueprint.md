# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Target Lean entry file: `FateXWork/Questions/Items05784E1F74/Main.lean`
- Status: Lean declarations drafted and file-verified; approved by openai-codex verifier

## Source Statement Inventory

### 0.5

- Planned Lean declarations: `approximateCaratheodory_asymptotically_tight_example`, `approximateCaratheodory_lowerBound_asymptotic_scalar`
- Source qualifiers: ['The source statement is formalized in `EuclideanSpace ℝ (Fin n)` so that the norm is the Euclidean/ℓ₂ norm, not the default sup norm on plain function spaces.', 'The main theorem includes `0 < n` because `ℝ^n` is treated as a positive-dimensional ambient Euclidean space for the displayed simplex construction.', 'The convex-combination lower bound is stated for `0 < k` and `k ≤ n`, the real-valued range in which `sqrt (1/k - 1/n)` has the intended nonnegative radicand.', 'Convex combinations of `k` possibly repeated points of `T` are encoded by coefficients `coeff : Fin k → ℝ` with nonnegativity and total sum equal to `1`, and selected points `y : Fin k → EuclideanSpace ℝ (Fin n)` lying in `T`.']
- Scope changes: ["Added hypotheses `0 < n`, `0 < k`, and `k ≤ n` to avoid degenerate dimensions and Lean's convention that `Real.sqrt` of a negative real is `0`.", 'The high-dimensional statement is separated as a scalar limit theorem for the lower-bound expression as `n → ∞` with fixed positive `k`.']
- Statement verification status: approved by openai-codex verifier
- Source proof / prover notes: No proof steps are provided; theorem bodies are intentionally `by sorry`.

Source statement:

0.5 KKK (Approximate Caratheodory is asymptotically tight) Demonstrate by example that the bound in Theorem 0.0.2 is almost tight. Specifically, for every $n$ find a set $T \subset \mathbb{R}^n$ and a point $x \in \operatorname{conv}(T)$ such that for any convex combination $\sum_{j=1}^k \lambda_j x_j$ of any $k$ points $x_1, \ldots, x_k \in T$, one has
$$
\left\|x - \sum_{j=1}^k \lambda_j x_j\right\|_2 \geq \sqrt{\frac{1}{k} - \frac{1}{n}}.
$$
Let $n \to \infty$ while keeping $k$ fixed to see that Theorem 0.0.2 is asymptotically tight in high dimensions.

Reference proof (optional hint):

0.5 Choose $T = \{ e _ { 1 } , \ldots , e _ { n } \}$ where $e _ { i }$ are the standard basis vectors. Then conv(T) is an $( n - 1 ) \cdot$ dimensional simplex; draw a picture for $n = 3$ . Let x be the center of the simplex. All tha remains is to calculate the distance from x to each $\left( k - 1 \right)$ -dimensional face of the simplex.
