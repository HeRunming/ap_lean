import Mathlib

set_option maxHeartbeats 4000000

namespace Problem3Component

abbrev LeftCosets (G : Type) [Group G] (H : Subgroup G) := G ⧸ H

abbrev RightCosets (G : Type) [Group G] (H : Subgroup G) :=
  Quotient (QuotientGroup.rightRel H)

def leftComponent (G : Type) [Group G] (H : Subgroup G) :
    LeftCosets G H → DoubleCoset.Quotient (H : Set G) H :=
  Quotient.lift (DoubleCoset.mk H H) (by
    intro x y hxy
    change QuotientGroup.leftRel H x y at hxy
    rw [QuotientGroup.leftRel_apply] at hxy
    apply (DoubleCoset.eq H H x y).2
    refine ⟨1, H.one_mem, x⁻¹ * y, ?_, ?_⟩
    · exact hxy
    · simp)

def rightComponent (G : Type) [Group G] (H : Subgroup G) :
    RightCosets G H → DoubleCoset.Quotient (H : Set G) H :=
  Quotient.lift (DoubleCoset.mk H H) (by
    intro x y hxy
    change QuotientGroup.rightRel H x y at hxy
    rw [QuotientGroup.rightRel_apply] at hxy
    apply (DoubleCoset.eq H H x y).2
    refine ⟨y * x⁻¹, ?_, 1, H.one_mem, ?_⟩
    · exact hxy
    · simp)

def CosetIncident (G : Type) [Group G] (H : Subgroup G) :
    LeftCosets G H → RightCosets G H → Prop :=
  fun q r ↦ ∃ g : G, QuotientGroup.mk g = q ∧ Quotient.mk'' g = r

lemma incident_component_eq
    (G : Type) [Group G] (H : Subgroup G)
    {q : LeftCosets G H} {r : RightCosets G H}
    (h : CosetIncident G H q r) :
    leftComponent G H q = rightComponent G H r := by
  rcases h with ⟨g, rfl, rfl⟩
  rfl

lemma component_eq_incident
    (G : Type) [Group G] (H : Subgroup G)
    {q : LeftCosets G H} {r : RightCosets G H}
    (h : leftComponent G H q = rightComponent G H r) :
    CosetIncident G H q r := by
  induction q using Quotient.inductionOn with
  | _ x =>
    induction r using Quotient.inductionOn with
    | _ y =>
      change DoubleCoset.mk H H x = DoubleCoset.mk H H y at h
      obtain ⟨a, ha, b, hb, hy⟩ := (DoubleCoset.eq H H x y).1 h
      refine ⟨x * b, ?_, ?_⟩
      · apply QuotientGroup.eq.2
        simpa using hb
      · apply Quotient.sound
        change QuotientGroup.rightRel H (x * b) y
        rw [QuotientGroup.rightRel_apply, hy]
        simpa [mul_assoc] using ha

lemma incident_iff_component_eq
    (G : Type) [Group G] (H : Subgroup G)
    {q : LeftCosets G H} {r : RightCosets G H} :
    CosetIncident G H q r ↔ leftComponent G H q = rightComponent G H r :=
  ⟨incident_component_eq G H, component_eq_incident G H⟩

def leftStabilizer (G : Type) [Group G] (H : Subgroup G) (g : G) : Subgroup H :=
  H.comap ((MulAut.conj g⁻¹).toMonoidHom.comp H.subtype)

def rightStabilizer (G : Type) [Group G] (H : Subgroup G) (g : G) : Subgroup H :=
  H.comap ((MulAut.conj g).toMonoidHom.comp H.subtype)

lemma relIndex_inf_eq_of_index_eq
    (G : Type) [Group G] (H K : Subgroup G)
    (hindex : H.index = K.index) (hK0 : K.index ≠ 0) :
    (H ⊓ K).relIndex H = (H ⊓ K).relIndex K := by
  have hH := Subgroup.relIndex_mul_index (H := H ⊓ K) (K := H) inf_le_left
  have hK := Subgroup.relIndex_mul_index (H := H ⊓ K) (K := K) inf_le_right
  rw [hindex] at hH
  exact Nat.eq_of_mul_eq_mul_right
    (Nat.pos_of_ne_zero hK0)
    (hH.trans hK.symm)

lemma conjugate_inf_relIndex_eq
    (G : Type) [Group G] (H : Subgroup G) [H.FiniteIndex] (g : G) :
    (H ⊓ H.map (MulAut.conj g).toMonoidHom).relIndex H =
      (H ⊓ H.map (MulAut.conj g).toMonoidHom).relIndex
        (H.map (MulAut.conj g).toMonoidHom) := by
  let K := H.map (MulAut.conj g).toMonoidHom
  have hmapindex : K.index = H.index := by
    change (H.map (MulAut.conj g).toMonoidHom).index = H.index
    exact Subgroup.index_map_equiv (H := H) (MulAut.conj g)
  have hindex : H.index = K.index := hmapindex.symm
  exact relIndex_inf_eq_of_index_eq G H K hindex
    (hindex ▸ Subgroup.FiniteIndex.index_ne_zero)

lemma leftFiber_eq_orbit
    (G : Type) [Group G] (H : Subgroup G) (g : G) :
    {q : LeftCosets G H | leftComponent G H q = DoubleCoset.mk H H g} =
      MulAction.orbit H (QuotientGroup.mk g : LeftCosets G H) := by
  ext q
  induction q using Quotient.inductionOn with
  | _ x =>
    constructor
    · intro hx
      change DoubleCoset.mk H H x = DoubleCoset.mk H H g at hx
      obtain ⟨a, ha, b, hb, hxb⟩ := (DoubleCoset.eq H H g x).1 hx.symm
      rw [MulAction.mem_orbit_iff]
      refine ⟨⟨a, ha⟩, ?_⟩
      apply QuotientGroup.eq.2
      rw [hxb]
      simpa [mul_assoc] using hb
    · intro hx
      rw [MulAction.mem_orbit_iff] at hx
      obtain ⟨a, ha⟩ := hx
      change QuotientGroup.mk ((a : H) * g) = QuotientGroup.mk x at ha
      change DoubleCoset.mk H H x = DoubleCoset.mk H H g
      apply ((DoubleCoset.eq H H g x).2 ?_).symm
      have hab : ((a : G) * g)⁻¹ * x ∈ H := QuotientGroup.eq.1 ha
      refine ⟨a, a.property, ((a : G) * g)⁻¹ * x, hab, ?_⟩
      group

instance rightCosetMulAction
    (G : Type) [Group G] (H : Subgroup G) : MulAction H (RightCosets G H) where
  smul h := Quotient.map' (fun x : G => x * (h : G)⁻¹) (by
    intro x y hxy
    rw [QuotientGroup.rightRel_apply] at hxy ⊢
    simpa [mul_assoc] using hxy)
  one_smul r := by
    induction r using Quotient.inductionOn with
    | _ x =>
      apply congrArg Quotient.mk''
      simp
  mul_smul h k r := by
    induction r using Quotient.inductionOn with
    | _ x =>
      apply congrArg Quotient.mk''
      simp [mul_inv_rev, mul_assoc]

lemma rightFiber_eq_orbit
    (G : Type) [Group G] (H : Subgroup G) (g : G) :
    {r : RightCosets G H | rightComponent G H r = DoubleCoset.mk H H g} =
      MulAction.orbit H (Quotient.mk'' g : RightCosets G H) := by
  ext r
  induction r using Quotient.inductionOn with
  | _ x =>
    constructor
    · intro hx
      change DoubleCoset.mk H H x = DoubleCoset.mk H H g at hx
      obtain ⟨a, ha, b, hb, hxb⟩ := (DoubleCoset.eq H H g x).1 hx.symm
      rw [MulAction.mem_orbit_iff]
      let k : H := ⟨b⁻¹, H.inv_mem hb⟩
      refine ⟨k, ?_⟩
      apply Quotient.sound
      change QuotientGroup.rightRel H (g * (k : G)⁻¹) x
      rw [QuotientGroup.rightRel_apply, hxb]
      simpa [k, mul_assoc] using ha
    · intro hx
      rw [MulAction.mem_orbit_iff] at hx
      obtain ⟨b, hb⟩ := hx
      change Quotient.mk'' (g * (b : G)⁻¹) = Quotient.mk'' x at hb
      change DoubleCoset.mk H H x = DoubleCoset.mk H H g
      apply ((DoubleCoset.eq H H g x).2 ?_).symm
      have hab : x * (g * (b : G)⁻¹)⁻¹ ∈ H := by
        have := Quotient.exact' hb
        rwa [QuotientGroup.rightRel_apply] at this
      refine ⟨x * (g * (b : G)⁻¹)⁻¹, hab, (b : G)⁻¹, H.inv_mem b.property, ?_⟩
      group

lemma leftFiber_ncard_eq_stabilizer_index
    (G : Type) [Group G] (H : Subgroup G) (g : G) :
    Set.ncard {q : LeftCosets G H |
      leftComponent G H q = DoubleCoset.mk H H g} =
      (MulAction.stabilizer H (QuotientGroup.mk g : LeftCosets G H)).index := by
  rw [leftFiber_eq_orbit]
  exact Nat.card_congr (MulAction.orbitEquivQuotientStabilizer H
    (QuotientGroup.mk g : LeftCosets G H))

lemma rightFiber_ncard_eq_stabilizer_index
    (G : Type) [Group G] (H : Subgroup G) (g : G) :
    Set.ncard {r : RightCosets G H |
      rightComponent G H r = DoubleCoset.mk H H g} =
      (MulAction.stabilizer H (Quotient.mk'' g : RightCosets G H)).index := by
  rw [rightFiber_eq_orbit]
  exact Nat.card_congr (MulAction.orbitEquivQuotientStabilizer H
    (Quotient.mk'' g : RightCosets G H))

lemma left_orbit_stabilizer_eq
    (G : Type) [Group G] (H : Subgroup G) (g : G) :
    MulAction.stabilizer H (QuotientGroup.mk g : LeftCosets G H) =
      leftStabilizer G H g := by
  ext h
  rw [MulAction.mem_stabilizer_iff]
  change QuotientGroup.mk ((h : G) * g) = QuotientGroup.mk g ↔ _
  rw [QuotientGroup.eq]
  simp only [leftStabilizer, Subgroup.mem_comap, MonoidHom.coe_comp,
    Function.comp_apply, Subgroup.coe_subtype, MulEquiv.coe_toMonoidHom,
    MulAut.conj_apply]
  constructor <;> intro hx
  · have := H.inv_mem hx
    simpa [mul_assoc] using this
  · have := H.inv_mem hx
    simpa [mul_assoc] using this

lemma right_orbit_stabilizer_eq
    (G : Type) [Group G] (H : Subgroup G) (g : G) :
    MulAction.stabilizer H (Quotient.mk'' g : RightCosets G H) =
      rightStabilizer G H g := by
  ext h
  rw [MulAction.mem_stabilizer_iff]
  change Quotient.mk'' (g * (h : G)⁻¹) = Quotient.mk'' g ↔ _
  rw [Quotient.eq'']
  change QuotientGroup.rightRel H (g * (h : G)⁻¹) g ↔
    g * (h : G) * g⁻¹ ∈ H
  rw [QuotientGroup.rightRel_apply]
  simp [mul_assoc]

lemma rightStabilizer_index_eq_relIndex
    (G : Type) [Group G] (H : Subgroup G) (g : G) :
    (rightStabilizer G H g).index =
      H.relIndex (H.map (MulAut.conj g).toMonoidHom) := by
  rw [rightStabilizer, Subgroup.index_comap]
  congr 2
  ext x
  simp [MonoidHom.mem_range, Subgroup.mem_map]

lemma leftStabilizer_index_eq_relIndex
    (G : Type) [Group G] (H : Subgroup G) (g : G) :
    (leftStabilizer G H g).index =
      (H.map (MulAut.conj g).toMonoidHom).relIndex H := by
  rw [leftStabilizer, Subgroup.index_comap]
  have hrange :
      (((MulAut.conj g⁻¹).toMonoidHom.comp H.subtype).range) =
        H.map (MulAut.conj g⁻¹).toMonoidHom := by
    ext x
    simp [MonoidHom.mem_range, Subgroup.mem_map]
  rw [hrange]
  symm
  have hrel := Subgroup.relIndex_map_map_of_injective
    (f := (MulAut.conj g).toMonoidHom) H
    (H.map (MulAut.conj g⁻¹).toMonoidHom) (MulAut.conj g).injective
  have hmap :
      (H.map (MulAut.conj g⁻¹).toMonoidHom).map (MulAut.conj g).toMonoidHom = H := by
    ext x
    constructor
    · rintro ⟨y, ⟨a, ha, rfl⟩, rfl⟩
      simpa [MulAut.conj_apply, mul_assoc] using ha
    · intro hx
      refine ⟨g⁻¹ * x * g, ?_, ?_⟩
      · exact ⟨x, hx, by simp [mul_assoc]⟩
      · simp [MulAut.conj_apply, mul_assoc]
  rw [hmap] at hrel
  exact hrel

lemma component_fiber_ncard_eq
    (G : Type) [Group G] (H : Subgroup G) [H.FiniteIndex] (g : G) :
    Set.ncard {q : LeftCosets G H |
      leftComponent G H q = DoubleCoset.mk H H g} =
    Set.ncard {r : RightCosets G H |
      rightComponent G H r = DoubleCoset.mk H H g} := by
  rw [leftFiber_ncard_eq_stabilizer_index, rightFiber_ncard_eq_stabilizer_index,
    left_orbit_stabilizer_eq, right_orbit_stabilizer_eq,
    leftStabilizer_index_eq_relIndex, rightStabilizer_index_eq_relIndex]
  simpa only [Subgroup.inf_relIndex_left, Subgroup.inf_relIndex_right] using
    conjugate_inf_relIndex_eq G H g

lemma component_fiber_ncard_eq_all
    (G : Type) [Group G] (H : Subgroup G) [H.FiniteIndex]
    (c : DoubleCoset.Quotient (H : Set G) H) :
    Set.ncard {q : LeftCosets G H | leftComponent G H q = c} =
    Set.ncard {r : RightCosets G H | rightComponent G H r = c} := by
  simpa [DoubleCoset.out_eq'] using component_fiber_ncard_eq G H c.out

noncomputable def componentFiberEquiv
    (G : Type) [Group G] (H : Subgroup G) [H.FiniteIndex]
    (c : DoubleCoset.Quotient (H : Set G) H) :
    {q : LeftCosets G H // leftComponent G H q = c} ≃
      {r : RightCosets G H // rightComponent G H r = c} := by
  classical
  letI : Fintype (LeftCosets G H) := Fintype.ofFinite _
  letI : Fintype (RightCosets G H) := Fintype.ofFinite _
  apply Fintype.equivOfCardEq
  rw [← Nat.card_eq_fintype_card, ← Nat.card_eq_fintype_card]
  exact component_fiber_ncard_eq_all G H c

noncomputable def compatibleCosetEquiv
    (G : Type) [Group G] (H : Subgroup G) [H.FiniteIndex] :
    LeftCosets G H ≃ RightCosets G H :=
  (Equiv.sigmaFiberEquiv (leftComponent G H)).symm |>.trans <|
    (Equiv.sigmaCongrRight (componentFiberEquiv G H)).trans <|
      Equiv.sigmaFiberEquiv (rightComponent G H)

lemma compatibleCosetEquiv_incident
    (G : Type) [Group G] (H : Subgroup G) [H.FiniteIndex]
    (q : LeftCosets G H) :
    CosetIncident G H q (compatibleCosetEquiv G H q) := by
  rw [incident_iff_component_eq]
  change leftComponent G H q = rightComponent G H
    ((componentFiberEquiv G H (leftComponent G H q)) ⟨q, rfl⟩)
  exact ((componentFiberEquiv G H (leftComponent G H q)) ⟨q, rfl⟩).property.symm

end Problem3Component
