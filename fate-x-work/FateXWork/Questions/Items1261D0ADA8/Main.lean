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
    ConvexOn 𝕜 s (fun x => t.sup' ht (fun i => f i x)) := by sorry
