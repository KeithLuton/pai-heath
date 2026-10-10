"""
lfm_bracket_vector.py

4D Bracketed Basis Float & BracketMix Epistemic Vector State for LFM.

Architecture:
- Every LFM "stir" (computational reasoning step) produces or operates on a 4-component BracketMix vector:
  BracketMix(unverified, speculative, explained, validated)
- Arithmetic operations (+, *) operate vectorially on all 4 basis components independently.
- Single scalar float projection (.scalar) occurs ONLY on-demand when an external interface explicitly requests a scalar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Sequence


class BracketType(str, Enum):

    UNVERIFIED = "unverified"
    SPECULATIVE = "speculative"
    EXPLAINED = "explained"
    VALIDATED = "validated"


# Epistemic rank order: lower = weaker claim. Used by the weakest-link
# (floor) rule. Ties and absence resolve DOWNWARD, never upward.
BRACKET_RANK = {
    BracketType.UNVERIFIED: 0,
    BracketType.SPECULATIVE: 1,
    BracketType.EXPLAINED: 2,
    BracketType.VALIDATED: 3,
}

# [{calibrated}] Floor mass threshold: components under this fraction of total
# absolute mass are ignored by floor_bracket_type. Chosen (not derived) at 1%
# so trace residue can't pin long chains to UNVERIFIED forever, while any
# material unverified contribution still demotes the label. See FLAGGED_CLAIMS.md.
FLOOR_MASS_FRACTION = 0.01


class BracketFloat(float):
    bracket_type: BracketType
    opening: str
    closing: str

    def __new__(
        cls,
        value: float,
        *,
        bracket_type: BracketType,
        opening: str,
        closing: str,
    ) -> "BracketFloat":
        obj = super().__new__(cls, value)
        obj.bracket_type = bracket_type
        obj.opening = opening
        obj.closing = closing
        return obj

    def wrap(self, text: str) -> str:
        return f"{self.opening}{text}{self.closing}"

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"value={float(self)}, "
            f"type={self.bracket_type.value!r})"
        )


UNVERIFIED_FLOAT = BracketFloat(
    1.0,
    bracket_type=BracketType.UNVERIFIED,
    opening="[{",
    closing="}]",
)

SPECULATIVE_FLOAT = BracketFloat(
    1.0,
    bracket_type=BracketType.SPECULATIVE,
    opening="[[",
    closing="]]",
)

EXPLAINED_FLOAT = BracketFloat(
    1.0,
    bracket_type=BracketType.EXPLAINED,
    opening="{[",
    closing="]}",
)

VALIDATED_FLOAT = BracketFloat(
    1.0,
    bracket_type=BracketType.VALIDATED,
    opening="[[[",
    closing="]]]",
)


@dataclass(frozen=True)
class BracketMix:
    unverified: float = 0.0
    speculative: float = 0.0
    explained: float = 0.0
    validated: float = 0.0

    def __add__(self, other: "BracketMix") -> "BracketMix":
        return BracketMix(
            unverified=self.unverified + other.unverified,
            speculative=self.speculative + other.speculative,
            explained=self.explained + other.explained,
            validated=self.validated + other.validated,
        )

    def __mul__(self, weight: float) -> "BracketMix":
        return BracketMix(
            unverified=self.unverified * weight,
            speculative=self.speculative * weight,
            explained=self.explained * weight,
            validated=self.validated * weight,
        )

    __rmul__ = __mul__

    @property
    def scalar(self) -> float:
        """On-demand scalar float projection of the 4D state vector."""
        return (
            self.unverified
            + self.speculative
            + self.explained
            + self.validated
        )

    def format_text(self, text: str) -> str:
        """Printed bracket is a trust claim to the reader -> uses the FLOOR
        (weakest link), never the dominant component."""
        floor = self.floor_bracket_type
        if floor == BracketType.VALIDATED:
            return f"[[[{text}]]]"
        elif floor == BracketType.EXPLAINED:
            return f"{{[{text}]}}"
        elif floor == BracketType.SPECULATIVE:
            return f"[[{text}]]"
        else:
            return f"[{{{text}}}]"

    @property
    def floor_bracket_type(self) -> BracketType:
        """
        Weakest-link epistemic status: the LOWEST-ranked bracket type carrying
        non-negligible mass. A chain is only as validated as its least
        validated input; validated mass can never wash out unverified mass.

        Components below FLOOR_MASS_FRACTION of total absolute mass are
        ignored so trace numerical residue in long chains does not permanently
        pin the label to UNVERIFIED. That threshold is a calibrated constant
        and is registered in FLAGGED_CLAIMS.md as [{calibrated}].
        Empty/zero vectors default to UNVERIFIED (never promote by absence).
        """
        components = [
            (BRACKET_RANK[BracketType.UNVERIFIED], BracketType.UNVERIFIED, self.unverified),
            (BRACKET_RANK[BracketType.SPECULATIVE], BracketType.SPECULATIVE, self.speculative),
            (BRACKET_RANK[BracketType.EXPLAINED], BracketType.EXPLAINED, self.explained),
            (BRACKET_RANK[BracketType.VALIDATED], BracketType.VALIDATED, self.validated),
        ]
        total_mass = sum(abs(value) for _, _, value in components)
        if total_mass <= 0.0:
            return BracketType.UNVERIFIED

        present = [
            (rank, kind)
            for rank, kind, value in components
            if abs(value) / total_mass >= FLOOR_MASS_FRACTION
        ]
        if not present:
            return BracketType.UNVERIFIED
        return min(present)[1]

    @property
    def dominant_bracket_type(self) -> BracketType:
        vals = [
            (self.validated, BracketType.VALIDATED),
            (self.explained, BracketType.EXPLAINED),
            (self.speculative, BracketType.SPECULATIVE),
            (self.unverified, BracketType.UNVERIFIED),
        ]
        vals.sort(key=lambda x: x[0], reverse=True)
        return vals[0][1]


BRACKET_FLOATS = {
    BracketType.UNVERIFIED: UNVERIFIED_FLOAT,
    BracketType.SPECULATIVE: SPECULATIVE_FLOAT,
    BracketType.EXPLAINED: EXPLAINED_FLOAT,
    BracketType.VALIDATED: VALIDATED_FLOAT,
}

BRACKET_BASIS = {
    BracketType.UNVERIFIED: BracketMix(unverified=1.0),
    BracketType.SPECULATIVE: BracketMix(speculative=1.0),
    BracketType.EXPLAINED: BracketMix(explained=1.0),
    BracketType.VALIDATED: BracketMix(validated=1.0),
}


@dataclass(frozen=True)
class LFMStateTuple:
    """Full 5-element LFM State Tuple: (psi, tau, xi, k, B)"""
    psi: float  # Magnitude / Amplitude
    tau: int  # Local Epistemic Time
    xi: str  # Epistemic Status
    k: float  # Wavenumber / Scale Coupling Factor
    B: BracketMix  # 4D Bracket Basis Vector

    def tensor_product(self, other: LFMStateTuple) -> LFMStateTuple:
        """
        Tensor product interaction (tensor_a x tensor_b) -> tensor_prime
        CRITICAL RULE: Combines full tuples without converting either operand to a scalar.
        """
        # 1. Amplitude coupling
        psi_prime = (self.psi**2 + other.psi**2)**0.5

        # 2. Local epistemic time advancement
        tau_prime = max(self.tau, other.tau) + 1

        # 3. Scale coupling: amplitude-weighted geometric mean.
        # [{calibrated -> derived}] Changed 2026-08 from the plain geometric
        # mean sqrt(k_a*k_b) so that scale momentum P = psi^2 * ln(k) becomes
        # an exactly conserved, composition-order-invariant charge (see
        # conservation_audit / FLAGGED_CLAIMS.md section G). Reduces to the
        # old rule exactly when psi_a == psi_b.
        from math import exp, log
        E_a = self.psi**2
        E_b = other.psi**2
        k_a = max(0.0001, self.k)
        k_b = max(0.0001, other.k)
        k_prime = exp((E_a * log(k_a) + E_b * log(k_b)) / (E_a + E_b))

        # 4. 4D BracketMix vector addition
        B_prime = self.B + other.B

        # 5. Epistemic status via weakest-link (floor) rule: the combined
        # state is only as validated as its least-validated material input.
        # (dominant_bracket_type remains available for weighting, but never
        # names the status -- that would let validated mass launder
        # unverified inputs.)
        floor = B_prime.floor_bracket_type
        if floor == BracketType.VALIDATED:
            xi_prime = "VALIDATED"
        elif floor == BracketType.EXPLAINED:
            xi_prime = "EXPLAINED"
        elif floor == BracketType.SPECULATIVE:
            xi_prime = "SPECULATIVE"
        else:
            xi_prime = "UNVERIFIED"

        return LFMStateTuple(
            psi=psi_prime,
            tau=tau_prime,
            xi=xi_prime,
            k=k_prime,
            B=B_prime,
        )

    def __matmul__(self, other: LFMStateTuple) -> LFMStateTuple:
        """Allows @ operator for tensor product state interaction: state_a @ state_b"""
        return self.tensor_product(other)


@dataclass(frozen=True)
class Stir:

    local_time: int
    value: float
    bracket: BracketType
    source: str = ""


@dataclass
class TemporalFloat:
    """
    TemporalFloat tracks local epistemic time tau_local and history across stirs.
    Every stir advances tau_local by 1 and records the local interaction delta.
    """
    value: float = 0.0
    local_time: int = 0
    history: list[Stir] = field(default_factory=list)

    def stir(
        self,
        contribution: float,
        bracket: BracketType,
        *,
        weight: float = 1.0,
        source: str = "",
    ) -> TemporalFloat:
        if not isfinite(contribution):
            raise ValueError("Contribution must be finite.")
        if not isfinite(weight):
            raise ValueError("Weight must be finite.")

        delta = contribution * weight
        next_time = self.local_time + 1
        next_value = self.value + delta

        return TemporalFloat(
            value=next_value,
            local_time=next_time,
            history=[
                *this_history_copy(self.history),
                Stir(
                    local_time=next_time,
                    value=delta,
                    bracket=bracket,
                    source=source,
                ),
            ],
        )

    def bracket_totals(self) -> BracketMix:
        """Reconstructs the 4D BracketMix state vector from local time history."""
        totals = {kind: 0.0 for kind in BracketType}
        for stir_item in self.history:
            totals[stir_item.bracket] += stir_item.value

        return BracketMix(
            unverified=totals[BracketType.UNVERIFIED],
            speculative=totals[BracketType.SPECULATIVE],
            explained=totals[BracketType.EXPLAINED],
            validated=totals[BracketType.VALIDATED],
        )


def this_history_copy(history_list: list[Stir]) -> list[Stir]:
    return list(history_list)


# ---------------------------------------------------------------------------
# Conservation ledger ("spigot invariant")
#
# The tensor algebra above admits three exactly conserved charges. For any
# composition tree over leaf states s_1..s_n, in ANY order:
#
#   E = psi^2              (amplitude energy)      E_root  = sum(E_leaf)
#   B = BracketMix         (epistemic provenance)  B_root  = sum(B_leaf)
#   P = psi^2 * ln(k)      (scale momentum)        P_root  = sum(P_leaf)
#
# xi is a function of B (floor rule), so conserving B conserves the basis of
# every status label. tau is deliberately NOT conserved: it is strictly
# monotone (the causal clock the charges are checked against).
#
# The coupling invariant tying amplitude to provenance:
#
#   F(psi, B) = psi^2 - |B|_1
#
# Under balance normalization (every stir enters with |B|_1 == psi^2),
# F == 0 at every leaf, and because both terms are additive, F == 0 at every
# composite node automatically. Any nonzero F pinpoints amplitude that
# entered the pipe without a provenance tag, or provenance that was dropped:
# nothing leaves the tap that didn't enter the pipe.
#
# These are theorems of this data structure's update rules — bookkeeping
# conservation laws in the double-entry-accounting sense — not claims about
# physical nature. Tagged accordingly in FLAGGED_CLAIMS.md section G.
# ---------------------------------------------------------------------------

from math import log as _log


def conserved_charges(state: LFMStateTuple) -> tuple[float, float, BracketMix]:
    """Return the additive charge vector (E, P, B) of a state."""
    E = state.psi**2
    P = E * _log(max(0.0001, state.k))
    return (E, P, state.B)


def balance_residual(state: LFMStateTuple) -> float:
    """F(psi, B) = psi^2 - |B|_1. Zero when amplitude is fully provenance-backed."""
    mass = (
        abs(state.B.unverified)
        + abs(state.B.speculative)
        + abs(state.B.explained)
        + abs(state.B.validated)
    )
    return state.psi**2 - mass


def balanced_leaf(
    psi: float,
    tau: int,
    k: float,
    bracket: BracketType,
) -> LFMStateTuple:
    """Construct a leaf state satisfying the balance normalization |B|_1 = psi^2,
    with all provenance mass in the stated bracket. Its xi follows from the floor."""
    mass = psi**2
    basis = {
        BracketType.UNVERIFIED: BracketMix(unverified=mass),
        BracketType.SPECULATIVE: BracketMix(speculative=mass),
        BracketType.EXPLAINED: BracketMix(explained=mass),
        BracketType.VALIDATED: BracketMix(validated=mass),
    }
    B = basis[bracket]
    return LFMStateTuple(psi=psi, tau=tau, xi=B.floor_bracket_type.value.upper(), k=k, B=B)


@dataclass(frozen=True)
class ConservationAudit:
    passed: bool
    energy_residual: float       # E_root - sum(E_leaf)
    momentum_residual: float     # P_root - sum(P_leaf)
    provenance_residual: float   # max componentwise |B_root - sum(B_leaf)|
    balance_residual: float      # F(root) = psi^2 - |B|_1 (0 iff fully tagged)
    tau_causal: bool             # tau_root within [max_leaf_tau+1, max_leaf_tau+(n-1)] (n>1)
    floor_monotone: bool         # rank(xi_root) <= min leaf floor rank (dilution excepted)
    dilution_occurred: bool      # floor rose only because sub-threshold mass was ignored
    reasons: list[str]


def conservation_audit(
    root: LFMStateTuple,
    leaves: Sequence[LFMStateTuple],
    *,
    tolerance: float = 1e-9,
) -> ConservationAudit:
    """
    Audit a composite state against its claimed inputs using the conserved
    charges. Any violation localizes tampering: energy_residual != 0 means
    amplitude was injected or dropped; provenance_residual != 0 means bracket
    mass was injected or dropped; balance_residual != 0 means amplitude and
    provenance no longer back each other; a non-causal tau means the clock
    was rewound; a non-monotone floor (absent dilution) means status was
    promoted rather than derived.
    """
    reasons: list[str] = []

    E_sum = sum(s.psi**2 for s in leaves)
    P_sum = sum(conserved_charges(s)[1] for s in leaves)
    E_root, P_root, B_root = conserved_charges(root)

    B_sum = BracketMix()
    for s in leaves:
        B_sum = B_sum + s.B

    energy_res = E_root - E_sum
    momentum_res = P_root - P_sum
    prov_res = max(
        abs(B_root.unverified - B_sum.unverified),
        abs(B_root.speculative - B_sum.speculative),
        abs(B_root.explained - B_sum.explained),
        abs(B_root.validated - B_sum.validated),
    )
    bal_res = balance_residual(root)

    if abs(energy_res) > tolerance:
        reasons.append(
            f"Energy charge violated: psi^2 differs from leaf sum by {energy_res:+.3e} "
            f"(amplitude injected or dropped without a tensor step)."
        )
    if abs(momentum_res) > tolerance:
        reasons.append(
            f"Scale momentum violated: psi^2*ln(k) differs from leaf sum by {momentum_res:+.3e} "
            f"(k was altered outside the coupling rule)."
        )
    if prov_res > tolerance:
        reasons.append(
            f"Provenance charge violated: BracketMix differs from leaf sum by {prov_res:.3e} "
            f"(epistemic mass created or destroyed)."
        )
    if abs(bal_res) > tolerance:
        reasons.append(
            f"Balance broken: F = psi^2 - |B|_1 = {bal_res:+.3e} "
            f"(amplitude not fully backed by tagged provenance)."
        )

    max_leaf_tau = max((s.tau for s in leaves), default=0)
    n = len(leaves)
    if n > 1:
        tau_causal = (max_leaf_tau + 1) <= root.tau <= (max_leaf_tau + (n - 1))
    else:
        tau_causal = root.tau >= max_leaf_tau
    if not tau_causal:
        reasons.append(
            f"tau non-causal: root tau {root.tau} outside "
            f"[{max_leaf_tau + 1}, {max_leaf_tau + max(1, n - 1)}] for {n} leaves."
        )

    min_leaf_rank = min(
        (BRACKET_RANK[s.B.floor_bracket_type] for s in leaves),
        default=BRACKET_RANK[BracketType.UNVERIFIED],
    )
    root_rank = BRACKET_RANK[B_root.floor_bracket_type]
    floor_monotone = root_rank <= min_leaf_rank
    dilution = False
    if not floor_monotone:
        # Distinguish threshold dilution (documented) from true promotion (tamper).
        strict_floor = _strict_floor_rank(B_root)
        if strict_floor <= min_leaf_rank:
            dilution = True
            floor_monotone = True
            reasons.append(
                "Floor rose via sub-threshold dilution (documented FLOOR_MASS_FRACTION "
                "behavior), not promotion. Weak-component mass is still present in B."
            )
        else:
            reasons.append(
                f"Floor monotonicity violated: root floor rank {root_rank} exceeds "
                f"weakest leaf rank {min_leaf_rank} and it is not threshold dilution "
                f"(status was promoted, not derived)."
            )

    passed = (
        abs(energy_res) <= tolerance
        and abs(momentum_res) <= tolerance
        and prov_res <= tolerance
        and abs(bal_res) <= tolerance
        and tau_causal
        and floor_monotone
    )
    if passed and not reasons:
        reasons.append("All conserved charges intact: E, P, B match leaf sums; F = 0; tau causal; floor monotone.")

    return ConservationAudit(
        passed=passed,
        energy_residual=energy_res,
        momentum_residual=momentum_res,
        provenance_residual=prov_res,
        balance_residual=bal_res,
        tau_causal=tau_causal,
        floor_monotone=floor_monotone,
        dilution_occurred=dilution,
        reasons=reasons,
    )


def _strict_floor_rank(B: BracketMix, epsilon: float = 1e-12) -> int:
    """Floor rank with NO mass-fraction threshold (any nonzero mass counts)."""
    present = [
        rank
        for rank, value in (
            (BRACKET_RANK[BracketType.UNVERIFIED], B.unverified),
            (BRACKET_RANK[BracketType.SPECULATIVE], B.speculative),
            (BRACKET_RANK[BracketType.EXPLAINED], B.explained),
            (BRACKET_RANK[BracketType.VALIDATED], B.validated),
        )
        if abs(value) > epsilon
    ]
    return min(present) if present else BRACKET_RANK[BracketType.UNVERIFIED]


if __name__ == "__main__":
    # Example: Trajectory with local time tau_local and stir history
    state = TemporalFloat()

    state = state.stir(0.7, BracketType.UNVERIFIED, source="new possibility")
    state = state.stir(0.5, BracketType.EXPLAINED, source="deductive relation")
    state = state.stir(0.9, BracketType.VALIDATED, source="measured constraint")

    print("Current Scalar Value:", state.value)       # 2.1
    print("Local Epistemic Time (tau):", state.local_time)  # 3
    print("Local History Steps:", state.history)
    print("Reconstructed 4D Bracket Vector:", state.bracket_totals())

