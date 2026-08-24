import Mathlib

open scoped BigOperators

/-- The closed unit ball for the `ℓ¹` norm on `Fin n → ℝ`. -/
def l1UnitBall (n : ℕ) : Set (Fin n → ℝ) :=
  {x | ∑ i, |x i| ≤ 1}

/-- The signed standard basis vector indexed by `i` and a sign bit. -/
def signedStandardBasis (n : ℕ) (i : Fin n) (b : Bool) : Fin n → ℝ :=
  if b then Pi.single i (1 : ℝ) else -(Pi.single i (1 : ℝ))

/-- The set of vertices `±e₁, …, ±eₙ` of the cross-polytope. -/
def crossPolytopeVertices (n : ℕ) : Set (Fin n → ℝ) :=
  Set.range (fun ib : Fin n × Bool => signedStandardBasis n ib.1 ib.2)

/-- Coefficients giving an explicit convex-combination representation of an `ℓ¹`-unit-ball point. -/
noncomputable def crossPolytopeCoeff (n : ℕ) (x : Fin n → ℝ) (anchor : Fin n)
    (i : Fin n) (b : Bool) : ℝ :=
  (if b then max (x i) 0 else max (-x i) 0) +
    if i = anchor then (1 - ∑ j, |x j|) / 2 else 0

/-- The `ℓ¹` unit ball is the convex hull of the signed standard basis vectors. -/
theorem l1UnitBall_eq_convexHull_crossPolytopeVertices (n : ℕ) (hn : 0 < n) :
    l1UnitBall n = convexHull ℝ (crossPolytopeVertices n) := by sorry

/-- Every point of the `ℓ¹` unit ball has the displayed convex-combination formula. -/
theorem l1UnitBall_explicit_convex_combination
    (n : ℕ) (hn : 0 < n) (x : Fin n → ℝ) (hx : x ∈ l1UnitBall n)
    (anchor : Fin n) :
    (∀ i b, 0 ≤ crossPolytopeCoeff n x anchor i b) ∧
      (∑ ib : Fin n × Bool, crossPolytopeCoeff n x anchor ib.1 ib.2 = 1) ∧
      x = ∑ ib : Fin n × Bool,
        crossPolytopeCoeff n x anchor ib.1 ib.2 •
          signedStandardBasis n ib.1 ib.2 := by sorry
