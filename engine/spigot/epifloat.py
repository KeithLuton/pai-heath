"""
lfm_epifloat.py — computing IN the bracket algebra, not beside it.

EpiFloat replaces float: write ordinary formulas and every result arrives
carrying its epistemic floor, its provenance mass vector B, and its causal
depth tau. No separate audit step for routine math — the algebra IS the
audit.

Semantics per binary operation (op in +, -, *, /, **):
    value  = op(a.value, b.value)          # normal IEEE arithmetic
    B      = a.B + b.B                     # provenance mass unions (additive)
    tau    = max(a.tau, b.tau) + 1         # causal clock, strictly monotone
    tag    = floor(B)                      # weakest link names the status

Ledger invariant (replaces the tensor algebra's E/P charges, which are
theorems of the ⊗ rule and do NOT survive ordinary arithmetic):
    Every atomic input carries unit provenance mass in its bracket, so for
    any result, |B|_1 == number of source facts that touched it.
    F = |B|_1 - n_leaves ≡ 0.  Nonzero F means provenance was dropped or
    injected. Verified by provenance_audit().

Auto-lift rule (the enforcement mechanism): any raw int/float entering a
mixed expression is lifted as UNVERIFIED with unit mass. Magic numbers
visibly demote every result they touch. Zero-cost constants (structural
zeros/ones from pure code shape) can be lifted mass-free via `structural`.

Known semantic caveats, stated plainly:
- Provenance mass is monotone: a - a == 0.0 in value but carries BOTH
  inputs' mass. B measures "how much tagged input touched this," not
  remaining information. Cancellation does not launder — by design.
- Comparisons act on value only; ordering ignores status.
- This makes bookkeeping unavoidable; it does not make values true.
Registered in FLAGGED_CLAIMS.md section I.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence, Union

from .bracket_vector import BracketMix, BracketType, BRACKET_RANK

Number = Union[int, float, "EpiFloat"]

_MAX_SOURCES = 16  # cap the source list; counts stay exact in B


@dataclass(frozen=True)
class EpiFloat:
    value: float
    B: BracketMix = field(default_factory=BracketMix)
    tau: int = 0
    sources: tuple[str, ...] = ()

    # ---- constructors ----------------------------------------------------

    @staticmethod
    def validated(value: float, source: str = "measured") -> "EpiFloat":
        return EpiFloat(float(value), BracketMix(validated=1.0), 0, (source,))

    @staticmethod
    def explained(value: float, source: str = "derived") -> "EpiFloat":
        return EpiFloat(float(value), BracketMix(explained=1.0), 0, (source,))

    @staticmethod
    def speculative(value: float, source: str = "hypothesis") -> "EpiFloat":
        return EpiFloat(float(value), BracketMix(speculative=1.0), 0, (source,))

    @staticmethod
    def unverified(value: float, source: str = "untagged") -> "EpiFloat":
        return EpiFloat(float(value), BracketMix(unverified=1.0), 0, (source,))

    # Calibrated constants (chosen-not-derived) are epistemically speculative
    # until independently validated; alias provided so intent reads clearly.
    calibrated = speculative

    @staticmethod
    def structural(value: float) -> "EpiFloat":
        """Mass-free lift for structural constants that carry no empirical
        claim (the 2 in x**2, a 0.0 accumulator). Adds no provenance mass and
        cannot change the floor."""
        return EpiFloat(float(value), BracketMix(), 0, ())

    @staticmethod
    def lift(x: Number, source: str = "raw literal") -> "EpiFloat":
        """Raw numbers enter as UNVERIFIED with unit mass. This is the
        enforcement rule: magic numbers demote what they touch."""
        if isinstance(x, EpiFloat):
            return x
        return EpiFloat.unverified(float(x), source)

    # ---- status ----------------------------------------------------------

    @property
    def floor(self) -> BracketType:
        return self.B.floor_bracket_type

    @property
    def tag(self) -> str:
        return self.B.format_text(f"{self.value:g}")

    @property
    def provenance_mass(self) -> float:
        return (
            abs(self.B.unverified)
            + abs(self.B.speculative)
            + abs(self.B.explained)
            + abs(self.B.validated)
        )

    def tap(self) -> tuple[float, str]:
        """Spigot: scalar on demand, status attached at the moment of exit."""
        return (self.value, self.tag)

    # ---- arithmetic core -------------------------------------------------

    def _combine(self, other: Number, value: float) -> "EpiFloat":
        o = EpiFloat.lift(other)
        merged_sources = (self.sources + o.sources)[:_MAX_SOURCES]
        return EpiFloat(
            value=float(value),
            B=self.B + o.B,
            tau=max(self.tau, o.tau) + 1,
            sources=merged_sources,
        )

    def __add__(self, other: Number) -> "EpiFloat":
        return self._combine(other, self.value + EpiFloat.lift(other).value)

    def __radd__(self, other: Number) -> "EpiFloat":
        return EpiFloat.lift(other).__add__(self)

    def __sub__(self, other: Number) -> "EpiFloat":
        return self._combine(other, self.value - EpiFloat.lift(other).value)

    def __rsub__(self, other: Number) -> "EpiFloat":
        return EpiFloat.lift(other).__sub__(self)

    def __mul__(self, other: Number) -> "EpiFloat":
        return self._combine(other, self.value * EpiFloat.lift(other).value)

    def __rmul__(self, other: Number) -> "EpiFloat":
        return EpiFloat.lift(other).__mul__(self)

    def __truediv__(self, other: Number) -> "EpiFloat":
        return self._combine(other, self.value / EpiFloat.lift(other).value)

    def __rtruediv__(self, other: Number) -> "EpiFloat":
        return EpiFloat.lift(other).__truediv__(self)

    def __pow__(self, other: Number) -> "EpiFloat":
        return self._combine(other, self.value ** EpiFloat.lift(other).value)

    def __neg__(self) -> "EpiFloat":
        return EpiFloat(-self.value, self.B, self.tau, self.sources)

    def __abs__(self) -> "EpiFloat":
        return EpiFloat(abs(self.value), self.B, self.tau, self.sources)

    # ---- comparisons on value only --------------------------------------

    def __lt__(self, other: Number) -> bool:
        return self.value < EpiFloat.lift(other).value

    def __le__(self, other: Number) -> bool:
        return self.value <= EpiFloat.lift(other).value

    def __gt__(self, other: Number) -> bool:
        return self.value > EpiFloat.lift(other).value

    def __ge__(self, other: Number) -> bool:
        return self.value >= EpiFloat.lift(other).value

    def close_to(self, other: Number, tol: float = 1e-9) -> bool:
        return abs(self.value - EpiFloat.lift(other).value) <= tol

    def __float__(self) -> float:
        return self.value

    def __repr__(self) -> str:
        return f"EpiFloat({self.tag}, floor={self.floor.value}, tau={self.tau}, mass={self.provenance_mass:g})"


# ---- ledger --------------------------------------------------------------


@dataclass(frozen=True)
class ProvenanceAudit:
    passed: bool
    mass_residual: float          # |B|_1 - n_leaves  (F; 0 when intact)
    floor_monotone: bool          # result floor <= weakest leaf floor
    tau_causal: bool
    reasons: list[str]


def provenance_audit(
    result: EpiFloat,
    leaves: Sequence[EpiFloat],
    *,
    tolerance: float = 1e-9,
) -> ProvenanceAudit:
    """F = |B|_1 - n_massive_leaves ≡ 0: every result's provenance mass must
    equal the count of unit-mass source facts that touched it. Structural
    (mass-free) lifts are excluded from the count by construction."""
    reasons: list[str] = []

    n_massive = sum(1 for leaf in leaves if leaf.provenance_mass > tolerance)
    residual = result.provenance_mass - n_massive
    if abs(residual) > tolerance:
        reasons.append(
            f"Provenance mass residual F = {residual:+.3e}: result mass "
            f"{result.provenance_mass:g} vs {n_massive} unit-mass source facts "
            f"(provenance dropped or injected)."
        )

    min_leaf_rank = min(
        (BRACKET_RANK[leaf.floor] for leaf in leaves if leaf.provenance_mass > tolerance),
        default=BRACKET_RANK[BracketType.UNVERIFIED],
    )
    floor_monotone = BRACKET_RANK[result.floor] <= min_leaf_rank
    if not floor_monotone:
        reasons.append(
            f"Floor promoted: result floor rank {BRACKET_RANK[result.floor]} exceeds "
            f"weakest source rank {min_leaf_rank}."
        )

    max_leaf_tau = max((leaf.tau for leaf in leaves), default=0)
    tau_causal = result.tau >= max_leaf_tau + (1 if len(leaves) > 1 else 0)
    if not tau_causal:
        reasons.append(f"tau non-causal: result tau {result.tau} <= max leaf tau {max_leaf_tau}.")

    passed = abs(residual) <= tolerance and floor_monotone and tau_causal
    if passed and not reasons:
        reasons.append("Provenance intact: mass matches source count; floor monotone; tau causal.")

    return ProvenanceAudit(passed, residual, floor_monotone, tau_causal, reasons)


# ---- self-test / demo ----------------------------------------------------

if __name__ == "__main__":
    V, E, S, U = EpiFloat.validated, EpiFloat.explained, EpiFloat.speculative, EpiFloat.unverified

    print("== Rule-of-mixtures density, Ti-6Al-4V, computed IN the algebra ==")
    # Elemental densities: handbook-validated. Mass fractions: spec sheet, explained.
    rho_ti = V(4.506, "handbook Ti density")
    rho_al = V(2.700, "handbook Al density")
    rho_v = V(6.110, "handbook V density")
    f_ti = E(0.90, "alloy spec")
    f_al = E(0.06, "alloy spec")
    f_v = E(0.04, "alloy spec")
    leaves_clean = [rho_ti, rho_al, rho_v, f_ti, f_al, f_v]

    rho = f_ti * rho_ti + f_al * rho_al + f_v * rho_v
    print("  rho =", repr(rho))
    a1 = provenance_audit(rho, leaves_clean)
    print("  audit:", a1.passed, "-", a1.reasons[0])

    print("\n== Same formula with one magic number: auto-lift demotes ==")
    rho_magic = f_ti * rho_ti + f_al * rho_al + f_v * 6.1  # raw literal!
    print("  rho =", repr(rho_magic))
    assert rho_magic.floor == BracketType.UNVERIFIED

    print("\n== Calibrated constant caps the floor at speculative ==")
    k_lfm = EpiFloat.calibrated(74.339, "LFM valence-manifold k (chosen)")
    scaled = rho * k_lfm / EpiFloat.structural(100.0)
    print("  scaled =", repr(scaled))
    assert scaled.floor == BracketType.SPECULATIVE

    print("\n== Cancellation does not launder ==")
    x = U(5.0, "guess")
    zero = x - x
    print("  x - x =", repr(zero), "(value 0, mass retained)")
    assert zero.provenance_mass == 2.0 and zero.floor == BracketType.UNVERIFIED

    print("\n== Tamper: provenance stripped, label kept ==")
    laundered = EpiFloat(rho_magic.value, BracketMix(validated=6.0), rho_magic.tau)
    a2 = provenance_audit(laundered, leaves_clean + [EpiFloat.lift(6.1)])
    print("  audit:", "CAUGHT" if not a2.passed else "MISSED")
    for r in a2.reasons:
        print("   -", r)
    assert not a2.passed

    print("\n== Structural constants are mass-free ==")
    area = V(3.0, "measured radius") ** EpiFloat.structural(2.0)
    print("  r^2 =", repr(area))
    assert area.floor == BracketType.VALIDATED and area.provenance_mass == 1.0

    print("\nAll EpiFloat assertions pass.")
