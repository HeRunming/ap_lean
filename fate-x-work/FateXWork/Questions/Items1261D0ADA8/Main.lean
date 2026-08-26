import Mathlib.Analysis.Convex.Function
import Mathlib.Data.Finset.Lattice.Fold

universe u v w z

theorem convexOn_finset_sup
    {𝕜 : Type u} {E : Type v} {β : Type w} {ι : Type z}
    [Semiring 𝕜] [PartialOrder 𝕜]
    [AddCommMonoid E] [AddCommMonoid β]
    [LinearOrder β] [IsOrderedAddMonoid β]
    [SMul 𝕜 E] [Module 𝕜 β] [PosSMulStrictMono 𝕜 β]
    {s : Set E} (t : Finset ι) (ht : t.Nonempty) (f : ι → E → β)
    (hf : ∀ i ∈ t, ConvexOn 𝕜 s (f i)) :
    ConvexOn 𝕜 s (fun x => t.sup' ht (fun i => f i x)) := by
  classical
  induction t using Finset.induction_on with
  | empty => simp at ht
  | @insert a t ha ih =>
    by_cases h : t.Nonempty
    · have H := (hf a (Finset.mem_insert_self a t)).sup
        (ih h (fun i hi => hf i (Finset.mem_insert_of_mem hi)))
      simpa only [Finset.sup'_insert h] using H
    · have h' : t = ∅ := Finset.not_nonempty_iff_eq_empty.mp h
      subst t
      simpa using hf a (Finset.mem_singleton_self a)
