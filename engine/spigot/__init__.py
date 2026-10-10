"""
Spigot Arithmetic Runtime (SAR) — the general computational substrate.

Domain-independent epistemic arithmetic: numbers and states that carry
provenance, never collapse until tapped, label by weakest link, and admit
conservation ledgers that make untagged input detectable.

Components:
  bracket_vector — BracketMix (4D provenance vector, floor rule),
                   BracketFloat/TemporalFloat (stir history, local tau),
                   LFMStateTuple + tensor product, conserved charges
                   (E, P, B), conservation_audit.
  epifloat       — EpiFloat: operator-overloaded epistemic number replacing
                   float; auto-lift of raw literals as UNVERIFIED;
                   provenance_audit (F = |B|_1 − source facts ≡ 0).

LFM Metallurgical Intelligence is the first application and testbed of this
runtime; the runtime itself carries no metallurgy and no physics claims.
"""
from .bracket_vector import *  # noqa: F401,F403
from .epifloat import *  # noqa: F401,F403
