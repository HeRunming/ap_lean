import Mathlib.Topology.MetricSpace.Pseudo.Defs

open Set

/-- `N` is an `ε`-net of `M` if `ε` is positive, `N` is contained in `M`,
    and every point of `M` is within distance `ε` of a point of `N`. -/
def IsENet {α : Type*} [PseudoMetricSpace α] (N M : Set α) (ε : ℝ) : Prop :=
  0 < ε ∧ N ⊆ M ∧ ∀ x ∈ M, ∃ y ∈ N, dist x y ≤ ε

/-- Transitivity of epsilon-nets. -/
theorem isENet_transitivity
    {α : Type*} [PseudoMetricSpace α]
    {N M K : Set α} {ε δ : ℝ}
    (hNM : IsENet N M ε)
    (hMK : IsENet M K δ) :
    IsENet N K (ε + δ) := by
  rcases hNM with ⟨hε, hNM, hcoverNM⟩
  rcases hMK with ⟨hδ, hMK, hcoverMK⟩
  refine ⟨add_pos hε hδ, hNM.trans hMK, ?_⟩
  intro x hx
  rcases hcoverMK x hx with ⟨y, hyM, hxy⟩
  rcases hcoverNM y hyM with ⟨z, hzN, hyz⟩
  refine ⟨z, hzN, ?_⟩
  calc
    dist x z ≤ dist x y + dist y z := dist_triangle x y z
    _ ≤ δ + ε := add_le_add hxy hyz
    _ = ε + δ := add_comm δ ε
