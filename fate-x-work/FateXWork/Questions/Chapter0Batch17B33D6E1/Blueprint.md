# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Target Lean entry file: `FateXWork/Questions/Chapter0Batch17B33D6E1/Main.lean`
- Status: planner preflight created; replace this with the agent's dependency plan.

## Planner Checklist

- [ ] Identify definitions and notation that must exist before theorem statements.
- [ ] Split large source theorems into Lean-sized lemmas.
- [ ] Record source labels/pages/equations for every generated declaration.
- [ ] Check local project and Mathlib names before introducing duplicates.
- [ ] Verify drafted Lean statements match the source document.
- [ ] Run independent statement/source verification review and apply corrections.
- [ ] Attach the complete source proof text when available, or explicitly record why it is unavailable.
- [ ] Record a natural-language proof strategy or source proof pointer for each theorem/lemma.
- [ ] Resolve all construction stubs before proof handoff.
- [ ] Mark stable theorem/lemma/example `sorry` declarations ready for a user-started prove workflow. (Only check after independent review approves every source entry.)

Replace all `_pending_` entries before drafting Lean. The managed workflow treats this initial
blueprint as a placeholder, not as a completed plan.

For each theorem or lemma, include proof guidance useful to the prover: the complete source proof
when available, relevant source proof paragraphs, induction variables, reductions, important
previously planned lemmas, and any known statement-fidelity caveats. Lean doc comments should include compact proof notes;
the generated supplemental blueprint skill carries the durable `Blueprint.md` reference.

## Import Plan

Direct Lean imports expected in generated Lean files only:
- `Mathlib`

## Suggested Search Modules

Non-gating modules or namespaces to search while proving. Do not force these into `.lean` imports unless the prover actually needs them.
- [none yet]

## Generated File Layout

- Aggregator entry file: `FateXWork/Questions/Chapter0Batch17B33D6E1/Main.lean`
- Split into `Basic.lean`, `Constructions.lean`, and `Theorems.lean` when declaration count, proof count, or source sections justify it.

## Source Statement Inventory

### 0.1

- Kind: question
- Source locator: `questions.json:pdf-pages-13,14`
- Planned Lean declarations: _pending_
- Dependencies: _pending_
- Formal statement review: _pending_
- Source qualifiers: _pending_
- Lean coverage: _pending_
- Scope changes: _pending_
- Statement verification status: _pending_
- Complete source proof: _pending_
- Source proof / prover notes: _pending_
- Source proof excerpt: 0.1 Recall that $\| x - y \| _ { 2 } ^ { 2 } = \| x \| _ { 2 } ^ { 2 } - 2 \langle x , y \rangle + \| y \| _ { 2 } ^ { 2 }$ . (This follows by expanding $\| x - y \| _ { 2 } ^ { 2 } = \langle x -$ $y , x - y \rangle . )$ Use this formula for $\| Z - \mathbb { E } Z \| _ { 2 } ^ { 2 } .$

0.1 KK (Two variance formulas)

(a) Recall that the variance of a random variable $X$ satisfies $\operatorname{Var}(X)=\mathbb{E}(X-\mathbb{E}X)^2=\mathbb{E}X^2-(\mathbb{E}X)^2$. Let us prove a higher-dimensional version of this identity. Check that any random vector $Z$ in $\mathbb{R}^n$ satisfies
$$
\mathbb{E}\|Z-\mathbb{E}Z\|_2^2=\mathbb{E}\|Z\|_2^2-\|\mathbb{E}Z\|_2^2.
$$

(b) Let $Z$ be a random vector in $\mathbb{R}^n$, and $Z'$ be an independent copy of $Z$, i.e. a random vector independent of $Z$ and with the same distribution as $Z$. Check that
$$
\mathbb{E}\|Z-\mathbb{E}Z\|_2^2=\frac{1}{2}\mathbb{E}\|Z-Z'\|_2^2.
$$

### 0.2

- Kind: question
- Source locator: `questions.json:pdf-pages-14`
- Planned Lean declarations: _pending_
- Dependencies: _pending_
- Formal statement review: _pending_
- Source qualifiers: _pending_
- Lean coverage: _pending_
- Scope changes: _pending_
- Statement verification status: _pending_
- Complete source proof: _pending_
- Source proof / prover notes: _pending_
- Source proof excerpt: 0.2 Check the identity $\mathbb { E } \Vert Z - a \Vert _ { 2 } ^ { 2 } - \mathbb { E } \Vert Z - \mu \Vert _ { 2 } ^ { 2 } = \Vert a - \mu \Vert _ { 2 } ^ { 2 }$ where $\mu = \mathbb { E } Z$

0.2 KKK (Expectation minimizes the mean squared error) The variance of a random variable $X$ has the following extremal property:
$$
\operatorname{Var}(X)=\mathbb{E}\min_{a\in\mathbb{R}}(X-a)^2.
$$
Let us prove a more general, high-dimensional version of this fact. Check that a random vector $Z$ in $\mathbb{R}^n$ with finite $\mathbb{E}\|Z\|_2^2$ satisfies
$$
\mathbb{E}\|Z-\mathbb{E}Z\|_2^2=\min_{a\in\mathbb{R}^n}\mathbb{E}\|Z-a\|_2^2.
$$

### 0.3

- Kind: question
- Source locator: `questions.json:pdf-pages-14`
- Planned Lean declarations: _pending_
- Dependencies: _pending_
- Formal statement review: _pending_
- Source qualifiers: _pending_
- Lean coverage: _pending_
- Scope changes: _pending_
- Statement verification status: _pending_
- Complete source proof: _pending_
- Source proof / prover notes: _pending_
- Source proof excerpt: [none detected by preflight]

0.3 KK (Variance of a sum) Recall that the variance of a sum of independent random variables equals the sum of variances. Let us prove a higher-dimensional version of this identity. Check that any independent mean-zero random vectors $Z_1, \ldots, Z_k$ in $\mathbb{R}^n$ satisfy
$$
\mathbb{E}\left\|\sum_{j=1}^k Z_j\right\|_2^2 = \sum_{j=1}^k \mathbb{E}\|Z_j\|_2^2.
$$

### 0.4

- Kind: question
- Source locator: `questions.json:pdf-pages-14`
- Planned Lean declarations: _pending_
- Dependencies: _pending_
- Formal statement review: _pending_
- Source qualifiers: _pending_
- Lean coverage: _pending_
- Scope changes: _pending_
- Statement verification status: _pending_
- Complete source proof: _pending_
- Source proof / prover notes: _pending_
- Source proof excerpt: 0.4 (a) Select the signs independently at random. Calculate the expected squared norm of the random vector $\pm x _ { 1 } \pm x _ { 2 } \pm \cdot \cdot \cdot \pm x _ { n }$ using Example 0.3.

0.4 KK (Balancing vectors) Let $x_1, \ldots, x_n$ be vectors in $\mathbb{R}^n$ that lie within the unit Euclidean ball centered at the origin.

(a) Prove that it is possible to assign a sign $\pm$ to each vector such that the sum $\pm x_1 \pm x_2 \pm \cdots \pm x_n$ lies within a Euclidean ball of radius $\sqrt{n}$ centered at the origin.

(b) Explain why the value $\sqrt{n}$ cannot be reduced in general.

### 0.5

- Kind: question
- Source locator: `questions.json:pdf-pages-14`
- Planned Lean declarations: _pending_
- Dependencies: _pending_
- Formal statement review: _pending_
- Source qualifiers: _pending_
- Lean coverage: _pending_
- Scope changes: _pending_
- Statement verification status: _pending_
- Complete source proof: _pending_
- Source proof / prover notes: _pending_
- Source proof excerpt: 0.5 Choose $T = \{ e _ { 1 } , \ldots , e _ { n } \}$ where $e _ { i }$ are the standard basis vectors. Then conv(T) is an $( n - 1 ) \cdot$ dimensional simplex; draw a picture for $n = 3$ . Let x be the center of the simplex. All tha remains is to calculate the distance from x to each $\left( k - 1 \right)$ -dimensional face of the simplex.

0.5 KKK (Approximate Caratheodory is asymptotically tight) Demonstrate by example that the bound in Theorem 0.0.2 is almost tight. Specifically, for every $n$ find a set $T \subset \mathbb{R}^n$ and a point $x \in \operatorname{conv}(T)$ such that for any convex combination $\sum_{j=1}^k \lambda_j x_j$ of any $k$ points $x_1, \ldots, x_k \in T$, one has
$$
\left\|x - \sum_{j=1}^k \lambda_j x_j\right\|_2 \geq \sqrt{\frac{1}{k} - \frac{1}{n}}.
$$
Let $n \to \infty$ while keeping $k$ fixed to see that Theorem 0.0.2 is asymptotically tight in high dimensions.

### 0.6

- Kind: question
- Source locator: `questions.json:pdf-pages-14`
- Planned Lean declarations: _pending_
- Dependencies: _pending_
- Formal statement review: _pending_
- Source qualifiers: _pending_
- Lean coverage: _pending_
- Scope changes: _pending_
- Statement verification status: _pending_
- Complete source proof: _pending_
- Source proof / prover notes: _pending_
- Source proof excerpt: 0.6 To prove the upper bound, multiply the sum of binomial coeficients by the quantity $( k / n ) ^ { k }$ replace this quantity by $( k / n ) ^ { j }$ in the left side, and use the binomial theorem. To prove the lower bound, use the definition of the binomial coeficient to express it as a product of k fractions; check that each fraction is lower bounded by $n / k .$

0.6 KK (Bounds on binomial coefficients) Prove the inequalities
$$
\left(\frac{n}{k}\right)^k \leq \binom{n}{k} \leq \sum_{j=0}^k \binom{n}{j} \leq \left(\frac{en}{k}\right)^k
$$
for any integers $1 \leq k \leq n$.

### 0.7

- Kind: question
- Source locator: `questions.json:pdf-pages-14,15`
- Planned Lean declarations: _pending_
- Dependencies: _pending_
- Formal statement review: _pending_
- Source qualifiers: _pending_
- Lean coverage: _pending_
- Scope changes: _pending_
- Statement verification status: _pending_
- Complete source proof: _pending_
- Source proof / prover notes: _pending_
- Source proof excerpt: 0.7 Recall the scaling property of the volume in $\mathbb { R } ^ { n }$ used in the beginning of the proof of Theorem 0.0.4: the ball of radius r has volume $r ^ { n }$ times the volume of the unit ball.

0.7 KK (Thin shell phenomenon) Let us prove a counterintuitive fact that most of the volume of the high-dimensional ball lies near the surface. Consider the points inside the unit Euclidean ball of $\mathbb{R}^n$ that lie within distance $5/n$ from the surface of the ball, see Figure 0.3. Prove that such points make up over 99% of the volume of the unit ball in $\mathbb{R}^n$.

### 0.8

- Kind: question
- Source locator: `questions.json:pdf-pages-15`
- Planned Lean declarations: _pending_
- Dependencies: _pending_
- Formal statement review: _pending_
- Source qualifiers: _pending_
- Lean coverage: _pending_
- Scope changes: _pending_
- Statement verification status: _pending_
- Complete source proof: _pending_
- Source proof / prover notes: _pending_
- Source proof excerpt: 0.8 Compute the CDF of $\| X \| _ { 2 } ,$ , deduce the probability density function by diferentiation, and then compute the expectation.

0.8 KK (Thin shell phenomenon, continued) Let $X$ be a random vector that is uniformly distributed in the Euclidean unit ball of $\mathbb{R}^n$. Prove that
$$
\mathbb{E}\|X\|_2 = \frac{n}{n+1}.
$$

### 0.9

- Kind: question
- Source locator: `questions.json:pdf-pages-15`
- Planned Lean declarations: _pending_
- Dependencies: _pending_
- Formal statement review: _pending_
- Source qualifiers: _pending_
- Lean coverage: _pending_
- Scope changes: _pending_
- Statement verification status: _pending_
- Complete source proof: _pending_
- Source proof / prover notes: _pending_
- Source proof excerpt: 0.9 First, improve the bound on the number of balls in Corollary 0.0.3 using the following fact from elementary combinatorics: the number of ways to choose an unordered subset of k elements from an N-element set, with possible repetitions, is $\binom { N + k - 1 } { k }$ . Substitute $k = k _ { 0 } =$ $n / \log ( e N / n )$ and use Exercise 0.6 to bound the binomial coeficient by $C ^ { n }$ . Then follow the proof of Theorem 0.0.4.

0.9 KKK (Carl-Pajor theorem) Let’s improve Theorem 0.0.4 by replacing $N$ with $N/n$. Let $P$ be a polytope with $N \geq n$ vertices, which is contained in the unit Euclidean ball of $\mathbb{R}^n$, denoted by $B$. Prove that
$$
\frac{\operatorname{Vol}(P)}{\operatorname{Vol}(B)} \leq \left(C \sqrt{\frac{\log(eN/n)}{n}}\right)^n
$$
where $C > 0$ is an absolute constant.
