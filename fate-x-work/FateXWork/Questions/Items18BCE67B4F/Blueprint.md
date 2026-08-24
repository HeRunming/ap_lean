# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Target Lean entry file: `FateXWork/Questions/Items18BCE67B4F/Main.lean`
- Status: Lean declarations drafted and file-verified; approved by openai-codex verifier

## Source Statement Inventory

### 1.8

- Planned Lean declarations: `StudentPair`, `random_graph_no_large_independent_set`
- Source qualifiers: ['The condition n ≥ 7 is retained.', 'Friendship edges are modeled as a random set of unordered two-element student subsets.', 'Independent fair friendships are represented by ProbabilityTheory.IsSetBernoulli edges Set.univ p μ together with (p : ℝ) = 1 / 2.', 'The strict inequality 2 log₂ n < |S| expresses subsets of more than 2 log₂ n students.', 'The conclusion is the negation of the existence of a large independent subset.']
- Scope changes: ['An IsProbabilityMeasure assumption is included because the source statement is a probability assertion.', 'A MeasurableSet assumption for the final no-large-independent-set event is included so that applying μ denotes the ordinary measurable-event probability.']
- Statement verification status: approved by openai-codex verifier
- Source proof / prover notes: ['The theorem body is intentionally left as by sorry.', 'A proof would use a union bound over candidate independent subsets and the probability 2^(-k choose 2) that a fixed k-subset contains no friendship edges.']

Source statement:

1.8 KK (Independent sets in random graphs) Call a group of people *independent* if no two members are friends. Suppose $n \geq 7$ students enroll in a class on high-dimensional probability, with each pair becoming friends independently with probability $1/2$. Show that, with probability at least $1 - 1/n$, this class has no independent subsets of more than $2 \log_2 n$ students.

Reference proof (optional hint):

1.8 What is the probability that a given subset of k students is independent? How many subsets consisting of k students are there? Answer these questions and use the union bound.
