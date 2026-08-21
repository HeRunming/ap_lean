import Mathlib.Analysis.Convex.Function
import Mathlib.Data.Finset.Lattice.Fold
import Mathlib.Data.Real.Basic

namespace FateXWork
namespace PilotQuestions
namespace Items1261D0ADA8

/-- Pointwise maximum of a nonempty finite family of real-valued functions. -/
abbrev pointwiseFinsetMax {ι E : Type*} (I : Finset ι) (hI : I.Nonempty)
    (f : ι → E → ℝ) : E → ℝ :=
  fun x => I.sup' hI (fun i => f i x)

/--
Source `HDP/source/pilot_questions.json`, item 1.2 (page locator 28):
"Check that the pointwise maximum of a finite number of convex functions is a convex function."

Source proof: no proof text is supplied in the scoped JSON slice or preflight theorem block.
Proof sketch: interpret the finite pointwise maximum as `Finset.sup'` over a nonempty finite
index set. Prove closure by induction on the finite family, using `ConvexOn.sup` for the binary
maximum step.
Prover notes: unfold `pointwiseFinsetMax`; `Finset.sup'_mem` with closure under `ConvexOn.sup`
may also package the induction, and `Finset.sup'_apply` rewrites function-valued finite suprema
pointwise if needed.
-/
theorem item_1_2_convexOn_pointwiseFinsetMax {ι E : Type*} [AddCommGroup E]
    [Module ℝ E] (I : Finset ι) (hI : I.Nonempty) (s : Set E)
    (f : ι → E → ℝ) (hf : ∀ i ∈ I, ConvexOn ℝ s (f i)) :
    ConvexOn ℝ s (pointwiseFinsetMax I hI f) := by
  induction hI using Finset.Nonempty.cons_induction with
  | singleton i =>
      simpa [pointwiseFinsetMax] using hf i (by simp)
  | cons i t hi ht ih =>
      rw [show pointwiseFinsetMax (t.cons i hi) (Finset.cons_nonempty hi) f =
          f i ⊔ pointwiseFinsetMax t ht f by
        funext x
        simp [pointwiseFinsetMax, Finset.sup'_cons ht]]
      exact (hf i (by simp)).sup (ih fun j hj => hf j (by simp [hj]))

end Items1261D0ADA8
end PilotQuestions
end FateXWork
