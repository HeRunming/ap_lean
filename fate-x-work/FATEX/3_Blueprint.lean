import FATEX.«3_Component»

namespace Problem3Blueprint

open Finset Function

abbrev LeftCosets (G : Type) [Group G] (H : Subgroup G) := G ⧸ H

abbrev RightCosets (G : Type) [Group G] (H : Subgroup G) :=
  Quotient (QuotientGroup.rightRel H)

/-- A left and a right coset are incident when they share a group element. -/
def CosetIncident (G : Type) [Group G] (H : Subgroup G) :
    LeftCosets G H → RightCosets G H → Prop :=
  fun q r ↦ ∃ g : G, QuotientGroup.mk g = q ∧ Quotient.mk'' g = r

/-- A common representative can be reconstructed from a compatible bijection
between left and right cosets.  This is the Mathlib-interface layer; the
mathematical content is isolated in the existence of the bijection. -/
lemma commonTransversal_of_compatibleEquiv
    (G : Type) [Group G] (H : Subgroup G)
    (e : LeftCosets G H ≃ RightCosets G H)
    (hcompat : ∀ q, ∃ g : G,
      QuotientGroup.mk g = q ∧ Quotient.mk'' g = e q) :
    ∃ S : Set G, Subgroup.IsComplement S H ∧ Subgroup.IsComplement H S := by
  classical
  choose representative hleft hright using hcompat
  let S : Set G := Set.range representative
  refine ⟨S, ?_, ?_⟩
  · rw [Subgroup.isComplement_subgroup_right_iff_existsUnique_quotientGroupMk]
    intro q
    let s : S := ⟨representative q, ⟨q, rfl⟩⟩
    refine ⟨s, hleft q, ?_⟩
    intro y hy
    rcases y.property with ⟨q', hq'⟩
    apply Subtype.ext
    have q'eq : q' = q :=
      (hleft q').symm.trans ((congrArg QuotientGroup.mk hq').trans hy)
    exact hq'.symm.trans (congrArg representative q'eq)
  · rw [Subgroup.isComplement_subgroup_left_iff_existsUnique_quotientMk'']
    intro r
    let q := e.symm r
    let s : S := ⟨representative q, ⟨q, rfl⟩⟩
    refine ⟨s, by simpa [s, q] using hright q, ?_⟩
    intro y hy
    rcases y.property with ⟨q', hq'⟩
    apply Subtype.ext
    have heq : e q' = r :=
      (hright q').symm.trans ((congrArg Quotient.mk'' hq').trans hy)
    have q'eq : q' = q := by
      simpa [q] using e.injective (heq.trans (e.apply_symm_apply r).symm)
    exact hq'.symm.trans (congrArg representative q'eq)

/-- Core mathematical obligation.  Double-coset components of the incidence
graph are balanced; consequently their union satisfies Hall's condition. -/
lemma cosetIncident_hall
    (G : Type) [Group G] (H : Subgroup G) [H.FiniteIndex] :
    ∀ A : Finset (LeftCosets G H),
      A.card ≤ Set.ncard {r : RightCosets G H | ∃ q ∈ A, CosetIncident G H q r} := by
  classical
  intro A
  letI : Finite (RightCosets G H) :=
    Finite.of_equiv (LeftCosets G H)
      (QuotientGroup.quotientRightRelEquivQuotientLeftRel H).symm
  let e := Problem3Component.compatibleCosetEquiv G H
  have he : ∀ q : LeftCosets G H, CosetIncident G H q (e q) := by
    intro q
    simpa [e, CosetIncident, Problem3Component.CosetIncident] using
      Problem3Component.compatibleCosetEquiv_incident G H q
  have ht : Set.Finite {r : RightCosets G H | ∃ q ∈ A, CosetIncident G H q r} :=
    Set.finite_univ.subset (Set.subset_univ _)
  simpa using Set.ncard_le_ncard_of_injOn
    (s := (A : Set (LeftCosets G H)))
    (t := {r : RightCosets G H | ∃ q ∈ A, CosetIncident G H q r})
    (ht := ht)
    e (fun q hq ↦ ⟨q, hq, he q⟩) e.injective.injOn

/-- Hall's theorem converts the cardinal inequality into a compatible
bijection between the finite left- and right-coset spaces. -/
lemma exists_compatible_coset_equiv
    (G : Type) [Group G] (H : Subgroup G) [H.FiniteIndex] :
    ∃ e : LeftCosets G H ≃ RightCosets G H,
      ∀ q, CosetIncident G H q (e q) := by
  classical
  letI : Fintype (LeftCosets G H) := Fintype.ofFinite _
  have hHall : ∀ A : Finset (LeftCosets G H),
      A.card ≤ #{r : RightCosets G H | ∃ q ∈ A, CosetIncident G H q r} := by
    intro A
    simpa [Set.ncard_eq_toFinset_card'] using cosetIncident_hall G H A
  obtain ⟨f, hf_injective, hf_incident⟩ :=
    (Fintype.all_card_le_filter_rel_iff_exists_injective (CosetIncident G H)).mp hHall
  have hcard : Fintype.card (LeftCosets G H) = Fintype.card (RightCosets G H) :=
    (QuotientGroup.card_quotient_rightRel H).symm
  have hf_bijective : Bijective f :=
    (Fintype.bijective_iff_injective_and_card f).mpr ⟨hf_injective, hcard⟩
  let e : LeftCosets G H ≃ RightCosets G H := Equiv.ofBijective f hf_bijective
  exact ⟨e, by simpa [e] using hf_incident⟩

theorem exists_leftCoset_rightCoset_representative
    (G : Type) [Group G] (H : Subgroup G) [H.FiniteIndex] :
    ∃ S : Set G, Subgroup.IsComplement S H ∧ Subgroup.IsComplement H S := by
  obtain ⟨e, he⟩ := exists_compatible_coset_equiv G H
  exact commonTransversal_of_compatibleEquiv G H e he

end Problem3Blueprint
