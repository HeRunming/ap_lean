# Formalization Blueprint: HDP/source/full/qa/questions.json

- Source: `HDP/source/full/qa/questions.json`
- Target Lean entry file: `FateXWork/Questions/Items112F78B3F0B/Main.lean`
- Status: Lean declarations drafted and file-verified; approved by openai-codex verifier

## Source Statement Inventory

### 1.12

- Planned Lean declarations: `interpolation_L1_Linfty`
- Source qualifiers: ['The exponent p is represented by ENNReal.', 'The source conditions 1 < p < ∞ are represented by hp_one : 1 < p and hp_top : p < ⊤.', 'Random-variable measurability is represented by hX : AEStronglyMeasurable X μ.']
- Scope changes: ['The informal Lp norms are formalized using MeasureTheory.eLpNorm, which takes values in ENNReal.', 'The underlying probability space is represented by an arbitrary measurable space equipped with a measure μ; the stated inequality does not require μ to be a probability measure.']
- Statement verification status: approved by openai-codex verifier
- Source proof / prover notes: ['The theorem body is intentionally left as by sorry.', 'The intended argument bounds |X|^(p - 1) by the L∞ norm and integrates.']

Source statement:

1.12 K (Interpolation between $L^1$ and $L^\infty$) We know the $L^p$ norm of any random variable $X$ is bounded by the $L^\infty$ norm. We can get an even better bound if we also know that the $L^1$ norm of $X$ is small. Show that
$$
\|X\|_{L^p} \leq \|X\|_{L^1}^{\frac{1}{p}} \|X\|_{L^\infty}^{1-\frac{1}{p}} \quad \text{for any } 1 < p < \infty.
$$

Reference proof (optional hint):

1.12 Write $\mathbb { E } | X | ^ { p }$ as E $[ | X | | X | ^ { p - 1 } ]$ and bound the second factor by its supremum.
