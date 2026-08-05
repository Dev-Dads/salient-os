"""SalienceOS control seam — where the salience interpreter meets the verifier.

The integration the whole design is about: subsystems publish salience → the
central `interpret()` issues a `Directive` → the `Directive`'s verification depth
governs how hard the `Verifier` checks the executed action → the governed outcome
gates clearance and adaptation. One loop, two invariants held at once:
  - salience influences; policy authorizes (the interpreter's P-01);
  - VERIFIED requires an executor-independent world fact (the verifier's M1);
and the seam adds a third: salience may only ESCALATE verification, never weaken
the policy-signed floor, and nothing is learned from an unverified action.

Discipline unchanged: stdlib-only, synchronous, zero deps; `decide()` is the pure
mutation-test target.
"""

from salienceos.control.outcome import (
    FULL,
    INDEPENDENT,
    NONE,
    RECEIPT,
    GovernedOutcome,
)
from salienceos.control.govern import (
    GOVERNOR_VERSION,
    achieved_level,
    decide,
    escalation_for,
    govern,
    stakes_for,
)

__all__ = [
    "NONE",
    "RECEIPT",
    "INDEPENDENT",
    "FULL",
    "GovernedOutcome",
    "GOVERNOR_VERSION",
    "achieved_level",
    "decide",
    "escalation_for",
    "govern",
    "stakes_for",
]
