import Mathlib

theorem subadditive_sub_bound {V : Type*} [AddCommGroup V] [Module ℝ V]
    (f : V → ℝ)
    (hf : ∀ x y : V, f (x + y) ≤ f x + f y) :
    ∀ x y : V, f x - f y ≤ f (x - y) := by
  intro x y
  have h : f x ≤ f (x - y) + f y := by
    simpa only [sub_add_cancel] using hf (x - y) y
  linarith
