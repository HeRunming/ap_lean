# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Target Lean entry file: `FateXWork/Questions/Items13E1BE4B26/Main.lean`
- Status: Lean declarations drafted and file-verified; approved by openai-codex verifier

## Source Statement Inventory

### 1.3

- Planned Lean declarations: `convex_subtype_iff_finite_jensen`, `jensen_finite_random_vector`
- Source qualifiers: ['Part (a) is formalized using Fin m as the finite index type.', 'Since f has subtype domain K, the weighted sum is represented by a subtype element z together with an equality of its ambient vector value to the weighted sum.', 'Part (b) formalizes expectation using Bochner integrals with respect to a probability measure.']
- Scope changes: ['Vectors in R^n are represented as Fin n → ℝ.', 'The convexity assumption on K is retained as hK.', 'Finite-valued random vectors are expressed by the hypothesis (Set.range X).Finite.']
- Statement verification status: approved by openai-codex verifier
- Source proof / prover notes: ['Both theorem bodies are intentionally left as by sorry.', 'No proof steps are included.']

Source statement:

1.3 KK (Jensen inequality)

(a) The definition of a convex function (1.1) involves convex combinations of two points $x$ and $y$. Let us extend it to arbitrarily many points. Let $K \subset \mathbb{R}^n$ be a convex subset. Prove that a function $f : K \to \mathbb{R}$ is convex if and only if the following holds. For any $m \in \mathbb{N}$, any vectors $x_i \in K$ and any numbers $\lambda_i \geq 0$ with $\sum_{i=1}^m \lambda_i = 1$, we have
$$
f\left(\sum_{i=1}^m \lambda_i x_i\right) \leq \sum_{i=1}^m \lambda_i f(x_i).
$$

(b) Let $X$ be a random vector in $\mathbb{R}^n$ that takes finitely many values, and let $f : \mathbb{R}^n \to \mathbb{R}$ be a convex function. Deduce from part (a) Jensen inequality:
$$
f(\mathbb{E} X) \leq \mathbb{E} f(X).
$$

Reference proof (optional hint):

1.3 (a) Use induction on m. At the induction step, represent $\textstyle \sum _ { i = 1 } ^ { m } \lambda _ { i } x _ { i }$ as a convex combination of two vectors, one of which is $x _ { m }$ and the other is some convex combination of $x _ { 1 } , \ldots , x _ { m - 1 }$
