# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Target Lean entry file: `FateXWork/Questions/Items22353C7CE10/Main.lean`
- Status: Lean declarations drafted and file-verified; approved by openai-codex verifier

## Source Statement Inventory

### 2.23

- Planned Lean declarations: `subgaussian_mgf_requires_zero_mean`
- Source qualifiers: ['The probability measure assumption formalizes that X is a random variable.', 'The subgaussian MGF bound is quantified over every real coefficient.', 'Finite exponential moments are explicitly required so that each displayed MGF expectation is a genuine Bochner integral.', 'The exponent normalization is exp (K ^ 2 * coeff ^ 2), without an additional division by 2.']
- Scope changes: ['Imports Mathlib.Probability.Moments.MGFAnalytic.', 'Opens MeasureTheory for Measure, Integrable, IsProbabilityMeasure, and integral notation.']
- Statement verification status: approved by openai-codex verifier
- Source proof / prover notes: ['The theorem body is intentionally left as by sorry.', 'No proof steps are included.']

Source statement:

2.23 KK (Subgaussian MGF requires zero mean) You might wonder why we assumed that $\mathbb{E} X = 0$ in property (iv) of Proposition 2.6.1. Show that any random variable $X$ satisfying this property *must have* zero mean.

Reference proof (optional hint):

2.23 Use Jensen inequality for the exponential function.
