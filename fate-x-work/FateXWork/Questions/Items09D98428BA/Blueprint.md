# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Target Lean entry file: `FateXWork/Questions/Items09D98428BA/Main.lean`
- Status: Lean declarations drafted and file-verified; approved by openai-codex verifier

## Source Statement Inventory

### 0.9

- Planned Lean declarations: `carl_pajor_volume_bound`
- Source qualifiers: ['C is an absolute positive real constant.', 'n and N are natural numbers with 0 < n and n ≤ N.', 'P is a subset of the n-dimensional Euclidean space.', 'The map v injectively indexes exactly the extreme points of P, so P has exactly N vertices.', 'P is the convex hull of its indexed vertices.', 'P is contained in the closed unit Euclidean ball.', "Volumes are represented by Mathlib's Lebesgue volume valued in ℝ≥0∞."]
- Scope changes: ['Added the condition 0 < n to exclude the zero-dimensional division-by-zero interpretation.', "Formalized 'P has N vertices' by requiring Set.extremePoints ℝ P = Set.range v together with Function.Injective v.", 'Used ENNReal.ofReal for the nonnegative real-valued right-hand side so that it can be compared with MeasureTheory.volume.']
- Statement verification status: approved by openai-codex verifier
- Source proof / prover notes: ['The theorem body is intentionally left as by sorry.', 'No proof steps are included.']

Source statement:

0.9 KKK (Carl-Pajor theorem) Let’s improve Theorem 0.0.4 by replacing $N$ with $N/n$. Let $P$ be a polytope with $N \geq n$ vertices, which is contained in the unit Euclidean ball of $\mathbb{R}^n$, denoted by $B$. Prove that
$$
\frac{\operatorname{Vol}(P)}{\operatorname{Vol}(B)} \leq \left(C \sqrt{\frac{\log(eN/n)}{n}}\right)^n
$$
where $C > 0$ is an absolute constant.

Reference proof (optional hint):

0.9 First, improve the bound on the number of balls in Corollary 0.0.3 using the following fact from elementary combinatorics: the number of ways to choose an unordered subset of k elements from an N-element set, with possible repetitions, is $\binom { N + k - 1 } { k }$ . Substitute $k = k _ { 0 } =$ $n / \log ( e N / n )$ and use Exercise 0.6 to bound the binomial coeficient by $C ^ { n }$ . Then follow the proof of Theorem 0.0.4.
